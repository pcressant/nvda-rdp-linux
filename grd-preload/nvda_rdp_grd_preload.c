/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 * From draft-nvda-orca-rdp faae513, experiment 10
 * (preload/nvda-channels-preload.c), with the IPC framing of the FreeRDP
 * plugin and WLog logging.
 *
 * The RDAccess dynamic virtual channels (NVDA-SPEECH, NVDA-BRAILLE) for an
 * unmodified GNOME Remote Desktop, as a library loaded with LD_PRELOAD into
 * its session daemon.
 *
 * GNOME Remote Desktop opens one virtual channel manager per RDP connection
 * with WTSOpenServerA and closes it with WTSCloseServer
 * (gnome-remote-desktop@ba1b66d: src/grd-session-rdp.c:1512, 1472). This
 * library interposes these two functions:
 *
 * - after the real WTSOpenServerA, it starts one thread per channel, which
 *   listens on a Unix socket in $XDG_RUNTIME_DIR/gnome-remote-desktop/;
 * - when a local program (the nvda-rdp server) connects, the thread opens
 *   the dynamic channel towards the RDP client with the public WinPR API
 *   (WTSVirtualChannelOpenEx) and relays messages both ways;
 * - before the real WTSCloseServer, it stops the threads.
 *
 * On the socket, each channel message travels in one frame: a 32-bit
 * big-endian length, then the message, as between the FreeRDP plugin and the
 * client. WinPR's server side reassembles each client message before
 * queueing it (FreeRDP libfreerdp/core/server.c, wts_read_drdynvc_data), so
 * a frame is exactly one DVC message in both directions.
 *
 * All WinPR functions are looked up in the process at run time, so they are
 * the very ones GNOME Remote Desktop uses: linking WinPR here could load a
 * second copy of it before GNOME Remote Desktop's own.
 *
 * Logging goes through WLog, tag "nvda-rdp.grd-preload", so that it follows
 * GNOME Remote Desktop's WLOG_LEVEL and WLOG_FILTER settings.
 */

#define _GNU_SOURCE

#include <arpa/inet.h>
#include <dlfcn.h>
#include <errno.h>
#include <poll.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/eventfd.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/un.h>
#include <unistd.h>

#include <winpr/wlog.h>
#include <winpr/wtsapi.h>

#define LOG_TAG "nvda-rdp.grd-preload"
#define N_CHANNELS 2
#define MAX_IPC_FRAME_SIZE (16UL * 1024UL * 1024UL)
#define OPEN_RETRY_MS 200
#define OPEN_TIMEOUT_MS 30000
#define READY_TIMEOUT_MS 10000
#define POLL_INTERVAL_MS 100
/* A local peer that stops in the middle of a frame is dropped after this. */
#define FRAME_TIMEOUT_S 5

typedef HANDLE (*OpenServerFn)(LPSTR);
typedef VOID (*CloseServerFn)(HANDLE);
typedef BOOL (*QuerySessionFn)(HANDLE, DWORD, WTS_INFO_CLASS, LPSTR *, DWORD *);
typedef HANDLE (*OpenExFn)(DWORD, LPSTR, DWORD);
typedef BOOL (*QueryFn)(HANDLE, WTS_VIRTUAL_CLASS, PVOID *, DWORD *);
typedef VOID (*FreeFn)(PVOID);
typedef BOOL (*WriteFn)(HANDLE, PCHAR, ULONG, PULONG);
typedef BOOL (*ReadFn)(HANDLE, ULONG, PCHAR, ULONG, PULONG);
typedef BOOL (*CloseFn)(HANDLE);
typedef int (*EventFdFn)(HANDLE);
typedef DWORD (*LastErrorFn)(void);
typedef wLog *(*LogGetFn)(LPCSTR);
typedef BOOL (*LogIsLevelActiveFn)(wLog *, DWORD);
typedef BOOL (*LogPrintFn)(wLog *, DWORD, DWORD, size_t, const char *,
                           const char *, ...);

/* The WinPR functions of the process, resolved once by resolve_winpr. */
static struct {
  OpenServerFn open_server;
  CloseServerFn close_server;
  QuerySessionFn query_session;
  OpenExFn open_ex;
  QueryFn query;
  FreeFn free_memory;
  WriteFn write;
  ReadFn read;
  CloseFn close;
  EventFdFn event_fd;
  LastErrorFn last_error;
  LogGetFn log_get;
  LogIsLevelActiveFn log_is_level_active;
  LogPrintFn log_print;
  wLog *log;
} winpr;

typedef struct Bridge Bridge;

/* One channel: its listening socket and the thread that serves it. Owned by
 * its Bridge. */
typedef struct {
  Bridge *bridge;
  const char *channel_name;
  char socket_path[108];
  int listen_fd;
  pthread_t thread;
  bool has_thread;
} Channel;

/* The channels of one RDP connection, from WTSOpenServerA to
 * WTSCloseServer. Allocated by bridge_new, freed by bridge_free. */
struct Bridge {
  HANDLE vcm;
  DWORD session_id;
  int stop_fd; /* eventfd, readable once the threads must stop */
  Channel channels[N_CHANNELS];
};

static const struct {
  const char *channel_name;
  const char *socket_name;
} channel_defs[N_CHANNELS] = {
    {"NVDA-SPEECH", "nvda-speech.sock"},
    {"NVDA-BRAILLE", "nvda-braille.sock"},
};

/* One RDP connection per session daemon; a second one is left alone. */
static pthread_mutex_t bridge_lock = PTHREAD_MUTEX_INITIALIZER;
static Bridge *current_bridge;

/* Logs one message through WLog, or to stderr when WLog is unavailable. */
__attribute__((format(printf, 2, 3))) static void
log_message(DWORD level, const char *format, ...) {
  char text[512];
  va_list args;

  if (winpr.log != NULL && !winpr.log_is_level_active(winpr.log, level))
    return;
  va_start(args, format);
  vsnprintf(text, sizeof(text), format, args);
  va_end(args);
  if (winpr.log == NULL) {
    fprintf(stderr, "[%s] %s\n", LOG_TAG, text);
    return;
  }
  winpr.log_print(winpr.log, WLOG_MESSAGE_TEXT, level, __LINE__, __FILE__,
                  __func__, "%s", text);
  /* WLog's console appender writes levels below WARN to stdout, which is
   * fully buffered when systemd connects it to the journal (WinPR 3.30:
   * libwinpr/utils/wlog/ConsoleAppender.c). Without this, those lines
   * reach the journal only in 4 KiB blocks. */
  if (level < WLOG_WARN)
    fflush(stdout);
}

static void *lookup(const char *name, bool next) {
  void *symbol = dlsym(next ? RTLD_NEXT : RTLD_DEFAULT, name);

  if (symbol == NULL)
    fprintf(stderr, "[%s] cannot find %s: %s\n", LOG_TAG, name, dlerror());
  return symbol;
}

/* Resolves the WinPR functions; called from the interposed functions, under
 * no lock: GNOME Remote Desktop opens its first server before any thread of
 * this library exists. Returns false when one is missing, and then the
 * library only passes the calls through. */
static bool resolve_winpr(void) {
  static int resolved;

  if (resolved != 0)
    return resolved > 0;

  winpr.open_server = (OpenServerFn)lookup("WTSOpenServerA", true);
  winpr.close_server = (CloseServerFn)lookup("WTSCloseServer", true);
  winpr.query_session =
      (QuerySessionFn)lookup("WTSQuerySessionInformationA", false);
  winpr.open_ex = (OpenExFn)lookup("WTSVirtualChannelOpenEx", false);
  winpr.query = (QueryFn)lookup("WTSVirtualChannelQuery", false);
  winpr.free_memory = (FreeFn)lookup("WTSFreeMemory", false);
  winpr.write = (WriteFn)lookup("WTSVirtualChannelWrite", false);
  winpr.read = (ReadFn)lookup("WTSVirtualChannelRead", false);
  winpr.close = (CloseFn)lookup("WTSVirtualChannelClose", false);
  winpr.event_fd = (EventFdFn)lookup("GetEventFileDescriptor", false);
  winpr.last_error = (LastErrorFn)lookup("GetLastError", false);
  winpr.log_get = (LogGetFn)lookup("WLog_Get", false);
  winpr.log_is_level_active =
      (LogIsLevelActiveFn)lookup("WLog_IsLevelActive", false);
  /* WLog_PrintMessage exists in every WinPR 3; WLog_PrintTextMessage only
   * since 3.17. Its unchecked format is always "%s" here. */
  winpr.log_print = (LogPrintFn)lookup("WLog_PrintMessage", false);

  resolved =
      (winpr.open_server && winpr.close_server && winpr.query_session &&
       winpr.open_ex && winpr.query && winpr.free_memory && winpr.write &&
       winpr.read && winpr.close && winpr.event_fd && winpr.last_error &&
       winpr.log_get && winpr.log_is_level_active && winpr.log_print)
          ? 1
          : -1;
  if (resolved > 0)
    winpr.log = winpr.log_get(LOG_TAG);
  return resolved > 0;
}

static bool stop_requested(Bridge *bridge) {
  struct pollfd pfd = {.fd = bridge->stop_fd, .events = POLLIN};

  return poll(&pfd, 1, 0) > 0;
}

static bool send_all(int fd, const void *data, size_t length) {
  const char *bytes = data;

  while (length > 0) {
    ssize_t sent = send(fd, bytes, length, MSG_NOSIGNAL);

    if (sent < 0 && errno == EINTR)
      continue;
    if (sent <= 0)
      return false;
    bytes += sent;
    length -= (size_t)sent;
  }
  return true;
}

static bool receive_all(int fd, void *data, size_t length) {
  char *bytes = data;

  while (length > 0) {
    ssize_t received = recv(fd, bytes, length, 0);

    if (received < 0 && errno == EINTR)
      continue;
    if (received <= 0)
      return false;
    bytes += received;
    length -= (size_t)received;
  }
  return true;
}

static int create_listen_socket(const char *path) {
  struct sockaddr_un address = {.sun_family = AF_UNIX};
  size_t length = strlen(path);
  int fd = -1;

  if (length >= sizeof(address.sun_path)) {
    errno = ENAMETOOLONG;
    return -1;
  }
  memcpy(address.sun_path, path, length + 1);

  fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
  if (fd < 0)
    return -1;

  unlink(path);
  if (bind(fd, (struct sockaddr *)&address, sizeof(address)) < 0 ||
      chmod(path, 0600) < 0 || listen(fd, 1) < 0) {
    int saved = errno;

    close(fd);
    errno = saved;
    return -1;
  }
  return fd;
}

/* 1 when the client opened the channel, 0 while it has not, -1 on error. */
static int channel_ready(HANDLE dvc) {
  BOOL *ready = NULL; /* allocated by WinPR, freed with WTSFreeMemory */
  DWORD length = 0;
  int is_ready = 0;

  if (!winpr.query(dvc, WTSVirtualChannelReady, (PVOID *)&ready, &length))
    return -1;
  is_ready = (ready != NULL && *ready) ? 1 : 0;
  winpr.free_memory(ready);
  return is_ready;
}

/* Waits ms milliseconds, unless the bridge stops or the local peer leaves.
 * Returns false in those two cases. */
static bool wait_or_abort(Channel *channel, int peer_fd, int ms) {
  struct pollfd pfds[2] = {
      {.fd = channel->bridge->stop_fd, .events = POLLIN},
      {.fd = peer_fd, .events = POLLRDHUP},
  };

  return poll(pfds, 2, ms) <= 0;
}

/* The dynamic channel can only be opened once the client has set up
 * drdynvc (ERROR_NOT_READY before: FreeRDP@b78c289,
 * libfreerdp/core/server.c:1568-1572). Retries while the local peer stays.
 * The returned handle is closed by relay with WTSVirtualChannelClose. */
static HANDLE open_channel(Channel *channel, int peer_fd) {
  for (int waited_ms = 0; waited_ms < OPEN_TIMEOUT_MS;
       waited_ms += OPEN_RETRY_MS) {
    HANDLE dvc =
        winpr.open_ex(channel->bridge->session_id, (LPSTR)channel->channel_name,
                      WTS_CHANNEL_OPTION_DYNAMIC);

    if (dvc != NULL)
      return dvc;
    if (waited_ms == 0)
      log_message(WLOG_DEBUG, "%s: channel not open yet (error %u), retrying",
                  channel->channel_name, (unsigned int)winpr.last_error());
    if (!wait_or_abort(channel, peer_fd, OPEN_RETRY_MS))
      return NULL;
  }
  log_message(WLOG_WARN, "%s: could not open the channel within %d ms",
              channel->channel_name, OPEN_TIMEOUT_MS);
  return NULL;
}

static bool wait_channel_ready(Channel *channel, HANDLE dvc, int peer_fd) {
  for (int waited_ms = 0; waited_ms < READY_TIMEOUT_MS;
       waited_ms += POLL_INTERVAL_MS) {
    int ready = channel_ready(dvc);

    if (ready != 0)
      return ready > 0;
    if (!wait_or_abort(channel, peer_fd, POLL_INTERVAL_MS))
      return false;
  }
  log_message(WLOG_WARN, "%s: the client did not open the channel within %d ms",
              channel->channel_name, READY_TIMEOUT_MS);
  return false;
}

/* The buffer of one relay, grown to the largest message seen. Owned by
 * relay, which frees data at the end. */
typedef struct {
  char *data;
  size_t size;
} Buffer;

static bool buffer_reserve(Buffer *buffer, size_t size) {
  char *data = NULL;

  if (size <= buffer->size)
    return true;
  data = realloc(buffer->data, size);
  if (data == NULL)
    return false;
  buffer->data = data;
  buffer->size = size;
  return true;
}

/* Reads one frame from the local peer and writes it to the client as one
 * DVC message. Returns false when the relay must end. */
static bool forward_frame_to_dvc(Channel *channel, HANDLE dvc, int peer_fd,
                                 Buffer *buffer) {
  uint32_t network_size = 0;
  uint32_t size = 0;
  ULONG written = 0;

  if (!receive_all(peer_fd, &network_size, sizeof(network_size)))
    return false;
  size = ntohl(network_size);
  if (size > MAX_IPC_FRAME_SIZE) {
    log_message(WLOG_ERROR, "%s: rejecting an oversized frame of %u bytes",
                channel->channel_name, (unsigned int)size);
    return false;
  }
  if (size == 0)
    return true;
  if (!buffer_reserve(buffer, size) ||
      !receive_all(peer_fd, buffer->data, size))
    return false;
  if (!winpr.write(dvc, buffer->data, size, &written) || written != size) {
    log_message(WLOG_ERROR, "%s: cannot write %u bytes to the channel",
                channel->channel_name, (unsigned int)size);
    return false;
  }
  log_message(WLOG_TRACE, "%s: %u bytes to the client", channel->channel_name,
              (unsigned int)size);
  return true;
}

/* Sends every message the client queued to the local peer, one frame each.
 * Returns false when the relay must end. */
static bool forward_dvc_messages(Channel *channel, HANDLE dvc, int peer_fd,
                                 Buffer *buffer) {
  ULONG size = 0;

  /* Without a buffer, WTSVirtualChannelRead gives the size of the next
   * message, or fails when there is none (FreeRDP server.c,
   * FreeRDP_WTSVirtualChannelRead). */
  while (winpr.read(dvc, 0, NULL, 0, &size)) {
    /* A read with a zero-size buffer never removes the message from the
     * queue, so an empty message is read with a one-byte buffer. */
    ULONG capacity = size > 0 ? size : 1;
    ULONG read = 0;
    uint32_t network_size = htonl((uint32_t)size);

    if (size > MAX_IPC_FRAME_SIZE || !buffer_reserve(buffer, capacity) ||
        !winpr.read(dvc, 0, buffer->data, capacity, &read) || read != size) {
      log_message(WLOG_ERROR, "%s: cannot read a message of %lu bytes",
                  channel->channel_name, (unsigned long)size);
      return false;
    }
    if (size == 0)
      continue;
    if (!send_all(peer_fd, &network_size, sizeof(network_size)) ||
        !send_all(peer_fd, buffer->data, size))
      return false;
    log_message(WLOG_TRACE, "%s: %lu bytes from the client",
                channel->channel_name, (unsigned long)size);
  }
  return true;
}

/* Gets the file descriptor that becomes readable when the client sent data.
 * It belongs to WinPR's event object: it must not be closed here. */
static int channel_event_fd(HANDLE dvc) {
  HANDLE *event_handle = NULL; /* freed with WTSFreeMemory */
  DWORD length = 0;
  int fd = -1;

  if (!winpr.query(dvc, WTSVirtualEventHandle, (PVOID *)&event_handle,
                   &length) ||
      event_handle == NULL)
    return -1;
  fd = winpr.event_fd(*event_handle);
  winpr.free_memory((PVOID)event_handle);
  return fd;
}

/* Relays one local peer, from the opening of the dynamic channel until one
 * side leaves or the bridge stops. Called by channel_thread. */
static void relay(Channel *channel, int peer_fd) {
  Bridge *bridge = channel->bridge;
  Buffer buffer = {0};
  HANDLE dvc = open_channel(channel, peer_fd);
  int event_fd = -1;

  if (dvc == NULL)
    return;
  if (!wait_channel_ready(channel, dvc, peer_fd))
    goto out;
  event_fd = channel_event_fd(dvc);
  if (event_fd < 0) {
    log_message(WLOG_ERROR, "%s: no event for the channel",
                channel->channel_name);
    goto out;
  }

  log_message(WLOG_INFO, "%s: channel open", channel->channel_name);
  for (;;) {
    struct pollfd pfds[3] = {
        {.fd = bridge->stop_fd, .events = POLLIN},
        {.fd = peer_fd, .events = POLLIN},
        {.fd = event_fd, .events = POLLIN},
    };
    int ret = poll(pfds, 3, 1000);

    if (ret < 0 && errno == EINTR)
      continue;
    if (ret < 0 || pfds[0].revents != 0)
      break;
    if (ret == 0) {
      /* Nothing for a second: check that the client still has it open. */
      if (channel_ready(dvc) <= 0)
        break;
      continue;
    }
    if ((pfds[1].revents & (POLLIN | POLLHUP | POLLERR)) != 0 &&
        !forward_frame_to_dvc(channel, dvc, peer_fd, &buffer))
      break;
    if ((pfds[2].revents & POLLIN) != 0 &&
        !forward_dvc_messages(channel, dvc, peer_fd, &buffer))
      break;
  }

out:
  log_message(WLOG_INFO, "%s: channel closed", channel->channel_name);
  winpr.close(dvc);
  free(buffer.data);
}

/* The thread of one channel: accepts one local peer at a time and relays
 * it. Started by bridge_new, joined by bridge_free. */
static void *channel_thread(void *data) {
  Channel *channel = data;
  Bridge *bridge = channel->bridge;
  const struct timeval frame_timeout = {.tv_sec = FRAME_TIMEOUT_S};

  while (!stop_requested(bridge)) {
    struct pollfd pfds[2] = {
        {.fd = bridge->stop_fd, .events = POLLIN},
        {.fd = channel->listen_fd, .events = POLLIN},
    };
    int peer_fd = -1;

    if (poll(pfds, 2, -1) < 0) {
      if (errno == EINTR)
        continue;
      break;
    }
    if (pfds[0].revents != 0)
      break;
    if ((pfds[1].revents & POLLIN) == 0)
      continue;

    peer_fd = accept4(channel->listen_fd, NULL, NULL, SOCK_CLOEXEC);
    if (peer_fd < 0)
      continue;
    /* A frame is read whole once it starts: bound the wait for its end. */
    if (setsockopt(peer_fd, SOL_SOCKET, SO_RCVTIMEO, &frame_timeout,
                   sizeof(frame_timeout)) < 0)
      log_message(WLOG_WARN, "%s: no receive timeout: %s",
                  channel->channel_name, strerror(errno));
    log_message(WLOG_DEBUG, "%s: local peer connected", channel->channel_name);
    relay(channel, peer_fd);
    close(peer_fd);
  }
  return NULL;
}

/* Creates the bridge of one virtual channel manager and starts its threads.
 * Called by WTSOpenServerA, with bridge_lock held. */
static Bridge *bridge_new(HANDLE vcm) {
  const char *runtime = getenv("XDG_RUNTIME_DIR");
  char directory[96];
  PULONG session_id = NULL; /* allocated by WinPR, freed with WTSFreeMemory */
  DWORD length = 0;
  Bridge *bridge = NULL;
  int written = 0;

  if (runtime == NULL) {
    log_message(WLOG_ERROR, "XDG_RUNTIME_DIR is not set");
    return NULL;
  }
  written = snprintf(directory, sizeof(directory), "%s/gnome-remote-desktop",
                     runtime);
  if (written < 0 || (size_t)written >= sizeof(directory)) {
    log_message(WLOG_ERROR, "XDG_RUNTIME_DIR is too long");
    return NULL;
  }
  if (!winpr.query_session(vcm, WTS_CURRENT_SESSION, WTSSessionId,
                           (LPSTR *)&session_id, &length) ||
      session_id == NULL) {
    log_message(WLOG_ERROR, "WTSQuerySessionInformationA failed");
    return NULL;
  }

  bridge = calloc(1, sizeof(Bridge));
  if (bridge == NULL) {
    winpr.free_memory(session_id);
    return NULL;
  }
  bridge->vcm = vcm;
  bridge->session_id = (DWORD)*session_id;
  winpr.free_memory(session_id);

  bridge->stop_fd = eventfd(0, EFD_CLOEXEC);
  if (bridge->stop_fd < 0) {
    free(bridge);
    return NULL;
  }

  if (mkdir(directory, 0700) < 0 && errno != EEXIST)
    log_message(WLOG_ERROR, "cannot create %s: %s", directory, strerror(errno));

  for (int i = 0; i < N_CHANNELS; i++) {
    Channel *channel = &bridge->channels[i];

    channel->bridge = bridge;
    channel->channel_name = channel_defs[i].channel_name;
    /* calloc left 0, a valid descriptor: bridge_free must not close it. */
    channel->listen_fd = -1;
    written = snprintf(channel->socket_path, sizeof(channel->socket_path),
                       "%s/%s", directory, channel_defs[i].socket_name);
    if (written < 0 || (size_t)written >= sizeof(channel->socket_path)) {
      log_message(WLOG_ERROR, "%s: socket path too long",
                  channel->channel_name);
      continue;
    }
    channel->listen_fd = create_listen_socket(channel->socket_path);
    if (channel->listen_fd < 0) {
      log_message(WLOG_ERROR, "cannot listen on %s: %s", channel->socket_path,
                  strerror(errno));
      continue;
    }
    if (pthread_create(&channel->thread, NULL, channel_thread, channel) != 0) {
      log_message(WLOG_ERROR, "%s: cannot start its thread",
                  channel->channel_name);
      continue;
    }
    channel->has_thread = true;
    log_message(WLOG_INFO, "%s: listening on %s", channel->channel_name,
                channel->socket_path);
  }
  return bridge;
}

/* Stops the threads, removes the sockets and frees the bridge. Called by
 * WTSCloseServer, without bridge_lock, before the real WTSCloseServer. */
static void bridge_free(Bridge *bridge) {
  uint64_t one = 1;

  if (write(bridge->stop_fd, &one, sizeof(one)) < 0)
    log_message(WLOG_ERROR, "cannot signal the channel threads to stop");

  for (int i = 0; i < N_CHANNELS; i++) {
    Channel *channel = &bridge->channels[i];

    if (channel->has_thread)
      pthread_join(channel->thread, NULL);
    if (channel->listen_fd >= 0) {
      close(channel->listen_fd);
      unlink(channel->socket_path);
    }
  }
  close(bridge->stop_fd);
  free(bridge);
}

/* Interposed WinPR functions, called by GNOME Remote Desktop. */

HANDLE WINAPI WTSOpenServerA(LPSTR pServerName) {
  HANDLE vcm = NULL;

  if (!resolve_winpr()) {
    OpenServerFn real = (OpenServerFn)dlsym(RTLD_NEXT, "WTSOpenServerA");

    return real != NULL ? real(pServerName) : NULL;
  }

  vcm = winpr.open_server(pServerName);
  if (vcm == NULL)
    return vcm;

  pthread_mutex_lock(&bridge_lock);
  if (current_bridge != NULL)
    log_message(WLOG_WARN,
                "a channel bridge already exists, not adding another one");
  else
    current_bridge = bridge_new(vcm);
  pthread_mutex_unlock(&bridge_lock);
  return vcm;
}

VOID WINAPI WTSCloseServer(HANDLE hServer) {
  Bridge *bridge = NULL;

  pthread_mutex_lock(&bridge_lock);
  if (current_bridge != NULL && current_bridge->vcm == hServer) {
    bridge = current_bridge;
    current_bridge = NULL;
  }
  pthread_mutex_unlock(&bridge_lock);

  /* Stop the relays before the channels they use go away. */
  if (bridge != NULL)
    bridge_free(bridge);

  if (resolve_winpr()) {
    winpr.close_server(hServer);
  } else {
    CloseServerFn real = (CloseServerFn)dlsym(RTLD_NEXT, "WTSCloseServer");

    if (real != NULL)
      real(hServer);
  }
}

#include <arpa/inet.h>
#include <ctype.h>
#include <errno.h>
#include <inttypes.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#include <freerdp/client/channels.h>
#include <freerdp/dvc.h>
#include <winpr/error.h>
#include <winpr/stream.h>

#define MAX_IPC_FRAME_SIZE (16UL * 1024UL * 1024UL)
/* Delay between two attempts to reach the client socket after it went away
 * (for example while the nvda-rdp-client service restarts). */
#define IPC_RETRY_DELAY_MS 1000
#define IPC_RETRY_STEP_MS 100

/* Set from the NVDA_RDP_DVC_DEBUG environment variable: log every block with
 * a hex dump.  Off by default, since blocks carry the spoken text and the
 * braille shown by the remote NVDA. */
static bool debug_enabled = false;

typedef struct {
  const char *channel_name;
  const char *socket_env;
  const char *socket_suffix;
} ChannelConfig;

static const ChannelConfig channel_configs[] = {
    {"NVDA-SPEECH", "NVDA_RDP_SOCKET", "/nvda-rdp-linux/speech.sock"},
    {"NVDA-BRAILLE", "NVDA_RDP_BRAILLE_SOCKET", "/nvda-rdp-linux/braille.sock"},
};

typedef struct {
  GENERIC_LISTENER_CALLBACK base;
  IWTSListener *listener;
  const ChannelConfig *config;
} NvdaRdpListenerCallback;

typedef struct {
  IWTSPlugin iface;
  NvdaRdpListenerCallback *listeners[2];
} NvdaRdpPlugin;

/* One open DVC.  Allocated by on_new_channel_connection, freed by on_close.
 *
 * socket_fd is shared by two threads: the IPC reader thread, which replaces it
 * when it reconnects, and FreeRDP's channel thread, which sends DVC blocks to
 * it in on_data_received.  Both read or change it only while holding lock.
 * closing tells the reader thread to stop instead of reconnecting. */
typedef struct {
  GENERIC_CHANNEL_CALLBACK base;
  pthread_mutex_t lock;
  int socket_fd;
  bool closing;
  pthread_t reader_thread;
  bool reader_started;
  const ChannelConfig *config;
} NvdaRdpChannelCallback;

static void log_prefix(void) {
  struct timespec now = {0};
  struct tm local = {0};

  timespec_get(&now, TIME_UTC);
  localtime_r(&now.tv_sec, &local);
  fprintf(stderr, "[%02d:%02d:%02d.%03ld] [nvda-rdp-dvc] ", local.tm_hour,
          local.tm_min, local.tm_sec, now.tv_nsec / 1000000L);
}

static bool send_all(int socket_fd, const void *buffer, size_t size) {
  const BYTE *position = buffer;

  while (size > 0) {
    ssize_t transferred = send(socket_fd, position, size, MSG_NOSIGNAL);
    if (transferred == 0)
      return false;
    if (transferred < 0) {
      if (errno == EINTR)
        continue;
      return false;
    }
    position += transferred;
    size -= (size_t)transferred;
  }
  return true;
}

static bool receive_all(int socket_fd, void *buffer, size_t size) {
  BYTE *position = buffer;

  while (size > 0) {
    ssize_t transferred = recv(socket_fd, position, size, 0);
    if (transferred == 0)
      return false;
    if (transferred < 0) {
      if (errno == EINTR)
        continue;
      return false;
    }
    position += transferred;
    size -= (size_t)transferred;
  }
  return true;
}

static bool send_ipc_frame(int socket_fd, const BYTE *payload, size_t size) {
  uint32_t network_size = 0;

  if (size > MAX_IPC_FRAME_SIZE)
    return false;
  network_size = htonl((uint32_t)size);
  return send_all(socket_fd, &network_size, sizeof(network_size)) &&
         send_all(socket_fd, payload, size);
}

/* Connect to the client socket of one channel; returns the descriptor, or -1
 * with errno set.  The caller owns the descriptor. */
static int connect_ipc_socket(const ChannelConfig *config) {
  const char *configured = getenv(config->socket_env);
  const char *runtime_dir = getenv("XDG_RUNTIME_DIR");
  struct sockaddr_un address = {.sun_family = AF_UNIX};
  int socket_fd = -1;
  int length = 0;

  if (configured != NULL)
    length =
        snprintf(address.sun_path, sizeof(address.sun_path), "%s", configured);
  else if (runtime_dir != NULL)
    length = snprintf(address.sun_path, sizeof(address.sun_path), "%s%s",
                      runtime_dir, config->socket_suffix);
  else
    return -1;

  if ((length < 0) || ((size_t)length >= sizeof(address.sun_path))) {
    errno = ENAMETOOLONG;
    return -1;
  }

  socket_fd = socket(AF_UNIX, SOCK_STREAM, 0);
  if (socket_fd < 0)
    return -1;
  if (connect(socket_fd, (const struct sockaddr *)&address, sizeof(address)) <
      0) {
    close(socket_fd);
    return -1;
  }

  log_prefix();
  fprintf(stderr, "connected IPC socket %s\n", address.sun_path);
  fflush(stderr);
  return socket_fd;
}

static bool is_closing(NvdaRdpChannelCallback *callback) {
  bool closing = false;

  pthread_mutex_lock(&callback->lock);
  closing = callback->closing;
  pthread_mutex_unlock(&callback->lock);
  return closing;
}

/* Forward client frames to the DVC until the socket fails.  Returns false if
 * the DVC itself refused a write, in which case reconnecting is pointless. */
static bool forward_ipc_frames(NvdaRdpChannelCallback *callback,
                               int socket_fd) {
  while (true) {
    uint32_t network_size = 0;
    uint32_t size = 0;
    BYTE *payload = NULL;
    UINT status = CHANNEL_RC_OK;

    if (!receive_all(socket_fd, &network_size, sizeof(network_size)))
      return true;
    size = ntohl(network_size);
    if (size > MAX_IPC_FRAME_SIZE) {
      log_prefix();
      fprintf(stderr, "rejecting oversized IPC frame: %" PRIu32 " bytes\n",
              size);
      return true;
    }

    if (size > 0) {
      payload = malloc(size);
      if (payload == NULL)
        return true;
      if (!receive_all(socket_fd, payload, size)) {
        free(payload);
        return true;
      }
    }

    status = callback->base.channel->Write(callback->base.channel, size,
                                           payload, NULL);
    free(payload);
    if (status != CHANNEL_RC_OK) {
      log_prefix();
      fprintf(stderr, "failed to forward IPC frame to DVC: 0x%08x\n", status);
      return false;
    }
    if (debug_enabled) {
      log_prefix();
      fprintf(stderr, "forwarded %" PRIu32 " IPC bytes to DVC\n", size);
      fflush(stderr);
    }
  }
}

/* Detach and close the current socket, unless on_close already owns it. */
static void drop_ipc_socket(NvdaRdpChannelCallback *callback) {
  int socket_fd = -1;

  pthread_mutex_lock(&callback->lock);
  if (!callback->closing) {
    socket_fd = callback->socket_fd;
    callback->socket_fd = -1;
  }
  pthread_mutex_unlock(&callback->lock);
  if (socket_fd >= 0)
    close(socket_fd);
}

/* Wait up to IPC_RETRY_DELAY_MS, then try to reach the client again.  The
 * wait is cut in short steps so that on_close is never delayed long.
 * Returns the new descriptor, or -1 if the channel is closing. */
static int reconnect_ipc_socket(NvdaRdpChannelCallback *callback) {
  const struct timespec step = {0, IPC_RETRY_STEP_MS * 1000000L};

  while (true) {
    int socket_fd = -1;

    for (int waited = 0; waited < IPC_RETRY_DELAY_MS;
         waited += IPC_RETRY_STEP_MS) {
      if (is_closing(callback))
        return -1;
      nanosleep(&step, NULL);
    }
    socket_fd = connect_ipc_socket(callback->config);
    if (socket_fd < 0)
      continue;

    pthread_mutex_lock(&callback->lock);
    if (callback->closing) {
      pthread_mutex_unlock(&callback->lock);
      close(socket_fd);
      return -1;
    }
    callback->socket_fd = socket_fd;
    pthread_mutex_unlock(&callback->lock);
    return socket_fd;
  }
}

/* IPC reader thread, started by on_open and joined by on_close.
 *
 * It owns the receiving side of the client socket.  When the client goes away
 * (service restart), it closes the socket and reconnects; the client then
 * starts a new handshake on the still open DVC. */
static void *ipc_reader(void *context) {
  NvdaRdpChannelCallback *callback = context;
  int socket_fd = 0;

  pthread_mutex_lock(&callback->lock);
  socket_fd = callback->socket_fd;
  pthread_mutex_unlock(&callback->lock);

  while (true) {
    if (socket_fd >= 0) {
      if (!forward_ipc_frames(callback, socket_fd))
        break;
      if (is_closing(callback))
        break;
      drop_ipc_socket(callback);
      log_prefix();
      fprintf(stderr, "IPC socket lost for channel %s; reconnecting\n",
              callback->config->channel_name);
      fflush(stderr);
    }
    socket_fd = reconnect_ipc_socket(callback);
    if (socket_fd < 0)
      break;
  }

  log_prefix();
  fputs("IPC reader stopped\n", stderr);
  fflush(stderr);
  return NULL;
}

static void log_block(const BYTE *bytes, size_t size) {
  log_prefix();
  fprintf(stderr, "received %zu bytes\n  hex:  ", size);
  for (size_t i = 0; i < size; ++i)
    fprintf(stderr, "%02x%s", bytes[i], (i + 1 == size) ? "" : " ");

  fputs("\n  ascii: |", stderr);
  for (size_t i = 0; i < size; ++i)
    fputc(isprint((unsigned char)bytes[i]) ? bytes[i] : '.', stderr);
  fputs("|\n", stderr);
  fflush(stderr);
}

/* FreeRDP callback, on its channel thread: one DVC block from the server. */
static UINT on_data_received(IWTSVirtualChannelCallback *channel_callback,
                             wStream *data) {
  NvdaRdpChannelCallback *callback = (NvdaRdpChannelCallback *)channel_callback;
  const BYTE *bytes = NULL;
  size_t size = 0;

  if ((channel_callback == NULL) || (data == NULL))
    return ERROR_INVALID_PARAMETER;

  bytes = Stream_ConstPointer(data);
  size = Stream_GetRemainingLength(data);
  if ((size > 0) && (bytes == NULL))
    return ERROR_INVALID_DATA;

  if (debug_enabled)
    log_block(bytes, size);

  /* While the client is unreachable, blocks are dropped: the client starts a
   * new handshake when it comes back.  A failed send only shuts the socket
   * down; the reader thread then notices it, closes it and reconnects. */
  pthread_mutex_lock(&callback->lock);
  if ((callback->socket_fd >= 0) &&
      !send_ipc_frame(callback->socket_fd, bytes, size))
    shutdown(callback->socket_fd, SHUT_RDWR);
  pthread_mutex_unlock(&callback->lock);

  return CHANNEL_RC_OK;
}

static UINT on_open(IWTSVirtualChannelCallback *channel_callback) {
  GENERIC_CHANNEL_CALLBACK *callback =
      (GENERIC_CHANNEL_CALLBACK *)channel_callback;
  NvdaRdpChannelCallback *nvda_callback =
      (NvdaRdpChannelCallback *)channel_callback;

  if (callback == NULL)
    return ERROR_INVALID_PARAMETER;
  if ((callback->channel == NULL) || (callback->channel->Write == NULL))
    return CHANNEL_RC_BAD_CHANNEL_HANDLE;

  /* If the client is not running yet, the reader thread keeps retrying. */
  nvda_callback->socket_fd = connect_ipc_socket(nvda_callback->config);
  if (nvda_callback->socket_fd < 0) {
    log_prefix();
    fprintf(stderr, "IPC unavailable for channel %s: %s; retrying\n",
            nvda_callback->config->channel_name, strerror(errno));
    fflush(stderr);
  }
  int thread_status = pthread_create(&nvda_callback->reader_thread, NULL,
                                     ipc_reader, nvda_callback);
  if (thread_status != 0) {
    log_prefix();
    fprintf(stderr, "failed to start IPC reader: %s\n",
            strerror(thread_status));
  } else {
    nvda_callback->reader_started = true;
  }

  log_prefix();
  fprintf(stderr, "channel %s opened\n", nvda_callback->config->channel_name);
  return (fflush(stderr) == 0) ? CHANNEL_RC_OK : ERROR_WRITE_FAULT;
}

static UINT on_close(IWTSVirtualChannelCallback *channel_callback) {
  NvdaRdpChannelCallback *callback = (NvdaRdpChannelCallback *)channel_callback;

  if (channel_callback == NULL)
    return ERROR_INVALID_PARAMETER;

  /* Stop the reader thread: closing prevents any reconnection, and shutting
   * the socket down wakes a blocked recv().  Once joined, this thread is the
   * only user of socket_fd. */
  pthread_mutex_lock(&callback->lock);
  callback->closing = true;
  if (callback->socket_fd >= 0)
    shutdown(callback->socket_fd, SHUT_RDWR);
  pthread_mutex_unlock(&callback->lock);
  if (callback->reader_started)
    pthread_join(callback->reader_thread, NULL);
  if (callback->socket_fd >= 0)
    close(callback->socket_fd);
  pthread_mutex_destroy(&callback->lock);

  log_prefix();
  fprintf(stderr, "channel %s closed\n", callback->config->channel_name);
  fflush(stderr);
  free(channel_callback);
  return CHANNEL_RC_OK;
}

static const IWTSVirtualChannelCallback callbacks = {
    .OnDataReceived = on_data_received,
    .OnOpen = on_open,
    .OnClose = on_close,
    .pInterface = NULL,
};

static UINT
on_new_channel_connection(IWTSListenerCallback *listener_callback,
                          IWTSVirtualChannel *channel, BYTE *data, BOOL *accept,
                          IWTSVirtualChannelCallback **channel_callback) {
  NvdaRdpListenerCallback *listener =
      (NvdaRdpListenerCallback *)listener_callback;
  NvdaRdpChannelCallback *callback = NULL;

  (void)data;
  if ((listener == NULL) || (channel == NULL) || (accept == NULL) ||
      (channel_callback == NULL))
    return ERROR_INVALID_PARAMETER;

  callback = calloc(1, sizeof(*callback));
  if (callback == NULL)
    return CHANNEL_RC_NO_MEMORY;
  if (pthread_mutex_init(&callback->lock, NULL) != 0) {
    free(callback);
    return CHANNEL_RC_NO_MEMORY;
  }

  callback->base.iface = callbacks;
  callback->base.plugin = listener->base.plugin;
  callback->base.channel_mgr = listener->base.channel_mgr;
  callback->base.channel = channel;
  callback->socket_fd = -1;
  callback->config = listener->config;
  listener->base.channel = channel;
  listener->base.channel_callback = &callback->base;
  *accept = TRUE;
  *channel_callback = &callback->base.iface;
  return CHANNEL_RC_OK;
}

static UINT plugin_initialize(IWTSPlugin *plugin_interface,
                              IWTSVirtualChannelManager *channel_manager) {
  NvdaRdpPlugin *plugin = (NvdaRdpPlugin *)plugin_interface;

  if ((plugin == NULL) || (channel_manager == NULL))
    return ERROR_INVALID_PARAMETER;

  for (size_t i = 0; i < ARRAYSIZE(channel_configs); ++i) {
    NvdaRdpListenerCallback *listener = calloc(1, sizeof(*listener));
    UINT status = CHANNEL_RC_OK;

    if (listener == NULL)
      return CHANNEL_RC_NO_MEMORY;
    listener->base.iface.OnNewChannelConnection = on_new_channel_connection;
    listener->base.plugin = plugin_interface;
    listener->base.channel_mgr = channel_manager;
    listener->config = &channel_configs[i];

    status = channel_manager->CreateListener(
        channel_manager, listener->config->channel_name, 0,
        &listener->base.iface, &listener->listener);
    if (status != CHANNEL_RC_OK) {
      free(listener);
      return status;
    }
    listener->listener->pInterface = plugin_interface->pInterface;
    plugin->listeners[i] = listener;
  }
  return CHANNEL_RC_OK;
}

static UINT plugin_terminated(IWTSPlugin *plugin_interface) {
  NvdaRdpPlugin *plugin = (NvdaRdpPlugin *)plugin_interface;

  if (plugin == NULL)
    return ERROR_INVALID_PARAMETER;

  for (size_t i = 0; i < ARRAYSIZE(plugin->listeners); ++i) {
    NvdaRdpListenerCallback *listener = plugin->listeners[i];
    if (listener == NULL)
      continue;
    if ((listener->base.channel_mgr != NULL) && (listener->listener != NULL))
      listener->base.channel_mgr->DestroyListener(listener->base.channel_mgr,
                                                  listener->listener);
    free(listener);
  }
  free(plugin);
  return CHANNEL_RC_OK;
}

FREERDP_ENTRY_POINT(
    UINT VCAPITYPE DVCPluginEntry(IDRDYNVC_ENTRY_POINTS *entry_points)) {
  static const char plugin_name[] = "nvda-rdp-dvc";
  NvdaRdpPlugin *plugin = NULL;

  if (entry_points == NULL)
    return ERROR_INVALID_PARAMETER;
  if ((entry_points->GetPlugin == NULL) ||
      (entry_points->RegisterPlugin == NULL))
    return ERROR_INVALID_PARAMETER;
  if (entry_points->GetPlugin(entry_points, plugin_name) != NULL)
    return CHANNEL_RC_ALREADY_INITIALIZED;
  debug_enabled = getenv("NVDA_RDP_DVC_DEBUG") != NULL;

  plugin = calloc(1, sizeof(*plugin));
  if (plugin == NULL)
    return CHANNEL_RC_NO_MEMORY;
  plugin->iface.Initialize = plugin_initialize;
  plugin->iface.Terminated = plugin_terminated;

  UINT status =
      entry_points->RegisterPlugin(entry_points, plugin_name, &plugin->iface);
  if (status != CHANNEL_RC_OK)
    free(plugin);
  return status;
}

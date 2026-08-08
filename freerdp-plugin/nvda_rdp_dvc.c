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

typedef struct {
  GENERIC_CHANNEL_CALLBACK base;
  int socket_fd;
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

static void *ipc_reader(void *context) {
  NvdaRdpChannelCallback *callback = context;

  while (true) {
    uint32_t network_size = 0;
    uint32_t size = 0;
    BYTE *payload = NULL;
    UINT status = CHANNEL_RC_OK;

    if (!receive_all(callback->socket_fd, &network_size, sizeof(network_size)))
      break;
    size = ntohl(network_size);
    if (size > MAX_IPC_FRAME_SIZE) {
      log_prefix();
      fprintf(stderr, "rejecting oversized IPC frame: %" PRIu32 " bytes\n",
              size);
      break;
    }

    if (size > 0) {
      payload = malloc(size);
      if (payload == NULL)
        break;
      if (!receive_all(callback->socket_fd, payload, size)) {
        free(payload);
        break;
      }
    }

    status = callback->base.channel->Write(callback->base.channel, size,
                                           payload, NULL);
    free(payload);
    if (status != CHANNEL_RC_OK) {
      log_prefix();
      fprintf(stderr, "failed to forward IPC frame to DVC: 0x%08x\n", status);
      break;
    }
    log_prefix();
    fprintf(stderr, "forwarded %" PRIu32 " IPC bytes to DVC\n", size);
    fflush(stderr);
  }

  log_prefix();
  fputs("IPC reader stopped\n", stderr);
  fflush(stderr);
  return NULL;
}

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

  log_prefix();
  if (fprintf(stderr, "received %zu bytes\n  hex:  ", size) < 0)
    return ERROR_WRITE_FAULT;

  for (size_t i = 0; i < size; ++i)
    fprintf(stderr, "%02x%s", bytes[i], (i + 1 == size) ? "" : " ");

  fputs("\n  ascii: |", stderr);
  for (size_t i = 0; i < size; ++i)
    fputc(isprint((unsigned char)bytes[i]) ? bytes[i] : '.', stderr);
  fputs("|\n", stderr);

  if (fflush(stderr) != 0)
    return ERROR_WRITE_FAULT;

  if ((callback->socket_fd >= 0) &&
      !send_ipc_frame(callback->socket_fd, bytes, size)) {
    log_prefix();
    fprintf(stderr, "failed to forward DVC block to IPC: %s\n",
            strerror(errno));
    fflush(stderr);
  }

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

  nvda_callback->socket_fd = connect_ipc_socket(nvda_callback->config);
  if (nvda_callback->socket_fd < 0) {
    log_prefix();
    fprintf(stderr, "IPC unavailable for channel %s: %s\n",
            nvda_callback->config->channel_name, strerror(errno));
    fflush(stderr);
  } else {
    int thread_status = pthread_create(&nvda_callback->reader_thread, NULL,
                                       ipc_reader, nvda_callback);
    if (thread_status != 0) {
      log_prefix();
      fprintf(stderr, "failed to start IPC reader: %s\n",
              strerror(thread_status));
      close(nvda_callback->socket_fd);
      nvda_callback->socket_fd = -1;
    } else {
      nvda_callback->reader_started = true;
    }
  }

  log_prefix();
  fprintf(stderr, "channel %s opened\n", nvda_callback->config->channel_name);
  return (fflush(stderr) == 0) ? CHANNEL_RC_OK : ERROR_WRITE_FAULT;
}

static UINT on_close(IWTSVirtualChannelCallback *channel_callback) {
  NvdaRdpChannelCallback *callback = (NvdaRdpChannelCallback *)channel_callback;

  if (channel_callback == NULL)
    return ERROR_INVALID_PARAMETER;

  if (callback->socket_fd >= 0) {
    shutdown(callback->socket_fd, SHUT_RDWR);
    if (callback->reader_started)
      pthread_join(callback->reader_thread, NULL);
    close(callback->socket_fd);
  }

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

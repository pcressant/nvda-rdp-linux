/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Test double of the WinPR functions the GNOME Remote Desktop preload looks
 * up, with the semantics of FreeRDP's server side where they matter
 * (libfreerdp/core/server.c: FreeRDP_WTSVirtualChannelRead returns the size
 * of the next message without a buffer, and a message is removed from the
 * queue once read entirely).
 *
 * The test drives it through the fake_* functions, with ctypes. Messages
 * pushed with fake_push play the RDP client; messages the preload writes are
 * recorded for fake_written.
 */

#define _GNU_SOURCE

#include <pthread.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/eventfd.h>
#include <unistd.h>

#include <winpr/wlog.h>
#include <winpr/wtsapi.h>

#define MAX_MESSAGES 64

typedef struct {
  char *data;
  size_t length;
} Message;

typedef struct {
  const char *name;
  int event_fd;
  BOOL ready;
  int open_failures;
  int opens;
  int closes;
  Message queue[MAX_MESSAGES];
  size_t queue_length;
  size_t offset; /* bytes of queue[0] already read */
  Message written[MAX_MESSAGES];
  size_t written_count;
} FakeChannel;

static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static FakeChannel channels[2] = {{.name = "NVDA-SPEECH", .event_fd = -1},
                                  {.name = "NVDA-BRAILLE", .event_fd = -1}};
static int vcm_objects[8]; /* one distinct handle per WTSOpenServerA */
static int server_opens;
static int server_closes;
static DWORD last_error;
static char log_text[65536];
static size_t log_length;

static FakeChannel *find(const char *name) {
  for (size_t i = 0; i < 2; i++)
    if (strcmp(channels[i].name, name) == 0)
      return &channels[i];
  return NULL;
}

static void signal_event(FakeChannel *channel) {
  uint64_t value = 0;

  if (channel->queue_length > 0) {
    value = 1;
    if (write(channel->event_fd, &value, sizeof(value)) < 0)
      abort();
  } else if (read(channel->event_fd, &value, sizeof(value)) < 0) {
    /* Already clear: EAGAIN on the non-blocking eventfd. */
  }
}

/* Control functions for the test. */

void fake_init(void) {
  for (size_t i = 0; i < 2; i++)
    channels[i].event_fd = eventfd(0, EFD_NONBLOCK | EFD_CLOEXEC);
}

void fake_set_ready(const char *name, int ready) {
  pthread_mutex_lock(&lock);
  find(name)->ready = ready ? TRUE : FALSE;
  pthread_mutex_unlock(&lock);
}

void fake_set_open_failures(const char *name, int failures) {
  pthread_mutex_lock(&lock);
  find(name)->open_failures = failures;
  pthread_mutex_unlock(&lock);
}

int fake_push(const char *name, const char *data, size_t length) {
  FakeChannel *channel = find(name);
  int pushed = 0;

  pthread_mutex_lock(&lock);
  if (channel->queue_length < MAX_MESSAGES) {
    Message *message = &channel->queue[channel->queue_length++];

    message->data = malloc(length > 0 ? length : 1);
    memcpy(message->data, data, length);
    message->length = length;
    signal_event(channel);
    pushed = 1;
  }
  pthread_mutex_unlock(&lock);
  return pushed;
}

/* Copies written message index into out; returns its length, or -1. */
long fake_written(const char *name, size_t index, char *out, size_t capacity) {
  FakeChannel *channel = find(name);
  long length = -1;

  pthread_mutex_lock(&lock);
  if (index < channel->written_count &&
      channel->written[index].length <= capacity) {
    memcpy(out, channel->written[index].data, channel->written[index].length);
    length = (long)channel->written[index].length;
  }
  pthread_mutex_unlock(&lock);
  return length;
}

int fake_written_count(const char *name) {
  int count = 0;

  pthread_mutex_lock(&lock);
  count = (int)find(name)->written_count;
  pthread_mutex_unlock(&lock);
  return count;
}

int fake_opens(const char *name) { return find(name)->opens; }

int fake_closes(const char *name) { return find(name)->closes; }

int fake_server_opens(void) { return server_opens; }

int fake_server_closes(void) { return server_closes; }

size_t fake_log(char *out, size_t capacity) {
  size_t length = 0;

  pthread_mutex_lock(&lock);
  length = log_length < capacity ? log_length : capacity;
  memcpy(out, log_text, length);
  pthread_mutex_unlock(&lock);
  return length;
}

/* WinPR functions. */

HANDLE WINAPI WTSOpenServerA(LPSTR server_name) {
  (void)server_name;
  return &vcm_objects[server_opens++ % 8];
}

VOID WINAPI WTSCloseServer(HANDLE server) {
  (void)server;
  server_closes++;
}

BOOL WINAPI WTSQuerySessionInformationA(HANDLE server, DWORD session,
                                        WTS_INFO_CLASS info, LPSTR *buffer,
                                        DWORD *length) {
  ULONG *id = NULL;

  (void)server;
  (void)session;
  if (info != WTSSessionId)
    return FALSE;
  id = malloc(sizeof(ULONG));
  *id = 7;
  *buffer = (LPSTR)id;
  *length = sizeof(ULONG);
  return TRUE;
}

VOID WINAPI WTSFreeMemory(PVOID memory) { free(memory); }

HANDLE WINAPI WTSVirtualChannelOpenEx(DWORD session, LPSTR name, DWORD flags) {
  FakeChannel *channel = find(name);
  HANDLE handle = NULL;

  pthread_mutex_lock(&lock);
  if (session == 7 && channel != NULL &&
      (flags & WTS_CHANNEL_OPTION_DYNAMIC) != 0) {
    if (channel->open_failures > 0) {
      channel->open_failures--;
      last_error = ERROR_NOT_READY;
    } else {
      channel->opens++;
      handle = channel;
    }
  }
  pthread_mutex_unlock(&lock);
  return handle;
}

BOOL WINAPI WTSVirtualChannelQuery(HANDLE handle, WTS_VIRTUAL_CLASS info,
                                   PVOID *buffer, DWORD *length) {
  FakeChannel *channel = handle;

  if (info == WTSVirtualChannelReady) {
    BOOL *ready = malloc(sizeof(BOOL));

    pthread_mutex_lock(&lock);
    *ready = channel->ready;
    pthread_mutex_unlock(&lock);
    *buffer = ready;
    *length = sizeof(BOOL);
    return TRUE;
  }
  if (info == WTSVirtualEventHandle) {
    HANDLE *event = malloc(sizeof(HANDLE));

    *event = channel;
    *buffer = event;
    *length = sizeof(HANDLE);
    return TRUE;
  }
  return FALSE;
}

int GetEventFileDescriptor(HANDLE handle) {
  return ((FakeChannel *)handle)->event_fd;
}

BOOL WINAPI WTSVirtualChannelRead(HANDLE handle, ULONG timeout, PCHAR buffer,
                                  ULONG size, PULONG read) {
  FakeChannel *channel = handle;
  Message *message = NULL;
  BOOL ok = TRUE;

  (void)timeout;
  pthread_mutex_lock(&lock);
  if (channel->queue_length == 0) {
    *read = 0;
    ok = FALSE;
    goto out;
  }
  message = &channel->queue[0];
  *read = (ULONG)(message->length - channel->offset);
  if (buffer == NULL || size == 0)
    goto out;
  if (*read > size)
    *read = size;
  memcpy(buffer, message->data + channel->offset, *read);
  channel->offset += *read;
  if (channel->offset >= message->length) {
    free(message->data);
    memmove(&channel->queue[0], &channel->queue[1],
            (channel->queue_length - 1) * sizeof(Message));
    channel->queue_length--;
    channel->offset = 0;
    signal_event(channel);
  }
out:
  pthread_mutex_unlock(&lock);
  return ok;
}

BOOL WINAPI WTSVirtualChannelWrite(HANDLE handle, PCHAR buffer, ULONG length,
                                   PULONG written) {
  FakeChannel *channel = handle;
  BOOL ok = FALSE;

  pthread_mutex_lock(&lock);
  if (channel->written_count < MAX_MESSAGES) {
    Message *message = &channel->written[channel->written_count++];

    message->data = malloc(length > 0 ? length : 1);
    memcpy(message->data, buffer, length);
    message->length = length;
    *written = length;
    ok = TRUE;
  }
  pthread_mutex_unlock(&lock);
  return ok;
}

BOOL WINAPI WTSVirtualChannelClose(HANDLE handle) {
  pthread_mutex_lock(&lock);
  ((FakeChannel *)handle)->closes++;
  pthread_mutex_unlock(&lock);
  return TRUE;
}

DWORD GetLastError(void) { return last_error; }

wLog *WLog_Get(LPCSTR name) {
  (void)name;
  return (wLog *)&vcm_objects[0];
}

BOOL WLog_IsLevelActive(wLog *log, DWORD level) {
  (void)log;
  (void)level;
  return TRUE;
}

BOOL WLog_PrintMessage(wLog *log, DWORD type, DWORD level, size_t line,
                       const char *file, const char *function, ...) {
  static const char *const names[] = {"TRACE", "DEBUG", "INFO",
                                      "WARN",  "ERROR", "FATAL"};
  const char *format = NULL;
  va_list args;
  int written = 0;

  (void)log;
  (void)line;
  (void)file;
  (void)function;
  if (type != WLOG_MESSAGE_TEXT || level > WLOG_FATAL)
    return FALSE;
  va_start(args, function);
  format = va_arg(args, const char *);
  pthread_mutex_lock(&lock);
  written = snprintf(log_text + log_length, sizeof(log_text) - log_length,
                     "%s ", names[level]);
  if (written > 0 && (size_t)written < sizeof(log_text) - log_length)
    log_length += (size_t)written;
  written = vsnprintf(log_text + log_length, sizeof(log_text) - log_length,
                      format, args);
  if (written > 0 && (size_t)written < sizeof(log_text) - log_length - 1) {
    log_length += (size_t)written;
    log_text[log_length++] = '\n';
  }
  pthread_mutex_unlock(&lock);
  va_end(args);
  return TRUE;
}

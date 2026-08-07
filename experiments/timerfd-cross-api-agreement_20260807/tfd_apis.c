/* One armed timerfd, queried through every Linux readiness/consumption API.
 *
 * THE QUESTION: do the APIs agree about the SAME timerfd? If two of them
 * disagree about whether it has expired -- or if one consumes an expiration the
 * others cannot see -- then expiry semantics diverge by access path, which is a
 * determinism bug regardless of which clock drives the timer.
 *
 * Readiness APIs are queried with a ZERO timeout BEFORE any consumption, so
 * each reports on identical state and none of them can consume. Only after all
 * readiness votes are collected does the program consume, once, and check that
 * the second consumer sees nothing left. */
#define _GNU_SOURCE
#include <errno.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/epoll.h>
#include <sys/select.h>
#include <sys/timerfd.h>
#include <sys/uio.h>
#include <unistd.h>

static int ready_poll(int fd) {
  struct pollfd p = {.fd = fd, .events = POLLIN};
  int r = poll(&p, 1, 0);
  return r > 0 && (p.revents & POLLIN) ? 1 : (r < 0 ? -1 : 0);
}
static int ready_ppoll(int fd) {
  struct pollfd p = {.fd = fd, .events = POLLIN};
  struct timespec z = {0, 0};
  int r = ppoll(&p, 1, &z, NULL);
  return r > 0 && (p.revents & POLLIN) ? 1 : (r < 0 ? -1 : 0);
}
static int ready_epoll(int fd) {
  int ep = epoll_create1(0);
  if (ep < 0) return -1;
  struct epoll_event ev = {.events = EPOLLIN, .data.fd = fd};
  if (epoll_ctl(ep, EPOLL_CTL_ADD, fd, &ev) != 0) { close(ep); return -1; }
  struct epoll_event out;
  int r = epoll_wait(ep, &out, 1, 0);
  close(ep);
  return r > 0 ? 1 : (r < 0 ? -1 : 0);
}
static int ready_select(int fd) {
  fd_set rs; FD_ZERO(&rs); FD_SET(fd, &rs);
  struct timeval z = {0, 0};
  int r = select(fd + 1, &rs, NULL, NULL, &z);
  return r > 0 && FD_ISSET(fd, &rs) ? 1 : (r < 0 ? -1 : 0);
}
static int ready_pselect(int fd) {
  fd_set rs; FD_ZERO(&rs); FD_SET(fd, &rs);
  struct timespec z = {0, 0};
  int r = pselect(fd + 1, &rs, NULL, NULL, &z, NULL);
  return r > 0 && FD_ISSET(fd, &rs) ? 1 : (r < 0 ? -1 : 0);
}

int main(void) {
  const int fd = timerfd_create(CLOCK_MONOTONIC, TFD_CLOEXEC | TFD_NONBLOCK);
  if (fd < 0) { perror("timerfd_create"); return 2; }
  /* 10ms one-shot, relative. */
  struct itimerspec t = {.it_interval = {0, 0}, .it_value = {0, 10 * 1000 * 1000}};
  if (timerfd_settime(fd, 0, &t, NULL) != 0) { perror("timerfd_settime"); return 2; }

  /* Block until expiry using a plain blocking wait that consumes nothing:
     poll with an infinite timeout on a duplicate is not needed -- poll does not
     consume, so poll on fd itself is safe and is the same authority under test. */
  struct pollfd wait_p = {.fd = fd, .events = POLLIN};
  if (poll(&wait_p, 1, 5000) <= 0) {
    fprintf(stderr, "timerfd never became ready within 5s\n");
    return 3;
  }

  /* All readiness APIs vote on identical, already-expired, unconsumed state. */
  const char *names[5] = {"poll", "ppoll", "epoll_wait", "select", "pselect6"};
  int votes[5];
  votes[0] = ready_poll(fd);
  votes[1] = ready_ppoll(fd);
  votes[2] = ready_epoll(fd);
  votes[3] = ready_select(fd);
  votes[4] = ready_pselect(fd);

  int agree = 0;
  for (int i = 0; i < 5; i++) {
    printf("readiness %-11s = %d\n", names[i], votes[i]);
    if (votes[i] == 1) agree++;
  }

  /* Consumption: readv first, then a scalar read. Exactly one must yield the
     expiration; the second must find nothing. Double-delivery or double-consume
     is the failure this checks. */
  uint64_t a = 0, b = 0;
  struct iovec iov = {.iov_base = &a, .iov_len = sizeof(a)};
  ssize_t n1 = readv(fd, &iov, 1);
  int e1 = errno;
  ssize_t n2 = read(fd, &b, sizeof(b));
  int e2 = errno;

  printf("consume readv       = %zd (val=%llu, errno=%s)\n", n1,
         (unsigned long long)a, n1 < 0 ? strerror(e1) : "-");
  printf("consume read        = %zd (val=%llu, errno=%s)\n", n2,
         (unsigned long long)b, n2 < 0 ? strerror(e2) : "-");

  int consumed_once = (n1 == (ssize_t)sizeof(a) && a >= 1) && (n2 < 0 && e2 == EAGAIN);
  printf("readiness agreement = %d of 5\n", agree);
  printf("consumption coherent = %s\n", consumed_once ? "yes" : "NO");
  printf("APIS AGREE = %d of 7\n", agree + (consumed_once ? 2 : 0));
  close(fd);
  return 0;
}

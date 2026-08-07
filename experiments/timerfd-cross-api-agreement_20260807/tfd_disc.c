/* Discriminator: does each API, ALONE, observe the same armed timerfd?
 * Mode is argv[1]. Each mode arms a fresh 10ms one-shot and uses exactly one
 * API, so no API can mask or consume for another. */
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

int main(int argc, char **argv) {
  const char *mode = argc > 1 ? argv[1] : "read";
  int fd = timerfd_create(CLOCK_MONOTONIC, TFD_CLOEXEC);
  if (fd < 0) { perror("create"); return 2; }
  struct itimerspec t = {{0, 0}, {0, 10 * 1000 * 1000}};
  if (timerfd_settime(fd, 0, &t, NULL) != 0) { perror("settime"); return 2; }

  if (!strcmp(mode, "read")) {
    uint64_t v = 0; ssize_t n = read(fd, &v, sizeof(v));
    printf("read rc=%zd val=%llu err=%s\n", n, (unsigned long long)v, n<0?strerror(errno):"-");
  } else if (!strcmp(mode, "readv")) {
    uint64_t v = 0; struct iovec io = {&v, sizeof(v)}; ssize_t n = readv(fd, &io, 1);
    printf("readv rc=%zd val=%llu err=%s\n", n, (unsigned long long)v, n<0?strerror(errno):"-");
  } else if (!strcmp(mode, "poll")) {
    struct pollfd p = {fd, POLLIN, 0}; int r = poll(&p, 1, 2000);
    printf("poll rc=%d revents=0x%x err=%s\n", r, p.revents, r<0?strerror(errno):"-");
  } else if (!strcmp(mode, "ppoll")) {
    struct pollfd p = {fd, POLLIN, 0}; struct timespec ts = {2, 0};
    int r = ppoll(&p, 1, &ts, NULL);
    printf("ppoll rc=%d revents=0x%x err=%s\n", r, p.revents, r<0?strerror(errno):"-");
  } else if (!strcmp(mode, "epoll")) {
    int ep = epoll_create1(0); struct epoll_event ev = {EPOLLIN, {.fd=fd}}, out;
    epoll_ctl(ep, EPOLL_CTL_ADD, fd, &ev);
    int r = epoll_wait(ep, &out, 1, 2000);
    printf("epoll rc=%d err=%s\n", r, r<0?strerror(errno):"-");
  } else if (!strcmp(mode, "select")) {
    fd_set rs; FD_ZERO(&rs); FD_SET(fd,&rs); struct timeval tv={2,0};
    int r = select(fd+1,&rs,NULL,NULL,&tv);
    printf("select rc=%d isset=%d err=%s\n", r, FD_ISSET(fd,&rs), r<0?strerror(errno):"-");
  } else if (!strcmp(mode, "pselect")) {
    fd_set rs; FD_ZERO(&rs); FD_SET(fd,&rs); struct timespec ts={2,0};
    int r = pselect(fd+1,&rs,NULL,NULL,&ts,NULL);
    printf("pselect rc=%d isset=%d err=%s\n", r, FD_ISSET(fd,&rs), r<0?strerror(errno):"-");
  }
  close(fd);
  return 0;
}

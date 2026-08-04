# Guests are compiled ONCE with a fixed optimization level. The variable under
# test is the HERMIT build profile, not the guest's — so guests are always -O2,
# identical across hermit profiles. Iteration counts are compile-time constants
# so every run does identical work.
CC ?= cc
CFLAGS ?= -O2
BIN := bin
GUESTS := $(BIN)/compute_bound $(BIN)/syscall_bound

all: $(GUESTS)

$(BIN):
	mkdir -p $(BIN)

$(BIN)/%: %.c | $(BIN)
	$(CC) $(CFLAGS) -o $@ $<

clean:
	rm -rf $(BIN)
.PHONY: all clean

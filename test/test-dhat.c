/* Focused exact-DHAT smoke and composition test (issue #238).

   #414: memory-events is compiled out unless MI_MEMEVT=1, while DHAT dispatches through
   the memory-events hook SITES (which survive whenever `MI_MEMEVT || MI_DHAT`). So the
   DHAT half of this test runs in both shapes, and the composition half -- the public
   `mi_memory_*` callback table observing the same events -- is asserted only where that
   API is real. The `MI_MEMEVT=0 MI_DHAT=1` build is exactly where the shared hook-site
   guard is load-bearing, which is why it is a CI row of its own (`dhat-on`).

   The same source is also built as `test-dhat-one-bucket`, against a collector compiled
   with DHAT_BUCKETS=1 so that every program point shares one hash chain, and with
   DHAT_FRAME_MAP_MIN_SLOTS=2 so that every dump grows its PC map. */
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include "mimalloc.h"
#include "mimalloc/memory-events.h"
#include "mimalloc/dhat.h"

/* Both variants run from the same directory, possibly at the same time, so each writes
   its dumps under its own name. */
#if defined(DHAT_TEST_ONE_BUCKET)
#define DHAT_TEST_FILE(name) "test-dhat-one-bucket-" name ".json"
#else
#define DHAT_TEST_FILE(name) "test-dhat-" name ".json"
#endif

#if defined(_MSC_VER)
#include <intrin.h>
#define DHAT_TEST_NOINLINE __declspec(noinline)
#define DHAT_TEST_RETURN_ADDRESS() _ReturnAddress()
#else
#define DHAT_TEST_NOINLINE __attribute__((noinline))
#define DHAT_TEST_RETURN_ADDRESS() __builtin_return_address(0)
#endif

#if defined(_WIN32)
static void test_setenv(const char* name, const char* value) { _putenv_s(name, value); }
static void test_unsetenv(const char* name) { _putenv_s(name, ""); }
#else
static void test_setenv(const char* name, const char* value) { setenv(name, value, 1); }
static void test_unsetenv(const char* name) { unsetenv(name); }
#endif

/* The fork scenarios park a thread inside its own armed DHAT event, which needs a
   memory-change callback to park in. */
#if !defined(_WIN32) && !defined(__wasi__) && MI_MEMEVT
#define DHAT_TEST_FORK 1
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#else
#define DHAT_TEST_FORK 0
#endif

typedef struct callback_counts_s { int alloc, free, resize; } callback_counts_t;
#if MI_MEMEVT
static void on_change(const mi_memory_change_t* change, void* arg) {
  callback_counts_t* counts = (callback_counts_t*)arg;
  if (change->kind == MI_MEMORY_ALLOCATE) counts->alloc++;
  else if (change->kind == MI_MEMORY_FREE) counts->free++;
  else if (change->kind == MI_MEMORY_RESIZE) counts->resize++;
}
static void install_callbacks(callback_counts_t* counts) {
  mi_memory_callbacks_t callbacks;
  memset(&callbacks, 0, sizeof(callbacks));
  callbacks.handlers[MI_MEMORY_ALLOCATE] = on_change; callbacks.args[MI_MEMORY_ALLOCATE] = counts;
  callbacks.handlers[MI_MEMORY_FREE] = on_change; callbacks.args[MI_MEMORY_FREE] = counts;
  callbacks.handlers[MI_MEMORY_RESIZE] = on_change; callbacks.args[MI_MEMORY_RESIZE] = counts;
  assert(mi_memory_set_callbacks(&callbacks));
}
#else
/* memory-events compiled out: its public API must still LINK, and must report itself off. */
static void assert_memevt_is_stubbed(void) {
  mi_memory_callbacks_t callbacks;
  memset(&callbacks, 0, sizeof(callbacks));
  assert(!mi_memory_tracking_set_enabled(true));
  assert(!mi_memory_tracking_is_enabled());
  assert(!mi_memory_set_callbacks(&callbacks));
  mi_memory_snapshot_t snap; memset(&snap, 0, sizeof(snap));
  snap.size = sizeof(snap); snap.version = MI_MEMORY_SNAPSHOT_VERSION;
  assert(!mi_memory_snapshot(&snap));
}
#endif

/* #549: MIMALLOC_DHAT=1 alone must start DHAT at process initialization. Run as its own
   CTest case (test-dhat-env-enabled) with the variable set through the ENVIRONMENT property,
   and deliberately never calls mi_dhat_start(): test-dhat and test-fork-locks-dhat-env both
   start DHAT explicitly or never look, which is how an ignored variable went unnoticed. */
static int run_env_enabled_check(void) {
  if (getenv("MIMALLOC_DHAT") == NULL) {
    fprintf(stderr, "test-dhat --env-enabled-check: MIMALLOC_DHAT not set in environment\n");
    return 1;
  }
  void* p = mi_malloc(16); assert(p != NULL);
  assert(mi_dhat_is_enabled());
  mi_dhat_stats_t_decl(stats);
  assert(mi_dhat_stats_get(&stats));
  assert(stats.enabled && stats.total_blocks >= 1 && stats.live_blocks >= 1);
  mi_free(p);
  mi_dhat_stop();
  puts("DHAT env-enabled check passed");
  return 0;
}

/* ---- frame table: every `fs` index must name the right `ftbl` entry ------------------
   Each recursion depth is one distinct stack, and so one program point. All of them share
   the hook-chain and driver PCs, and depths >= 1 repeat the recursive call's PC. Each
   allocation has its own size, so a program point's `tb` names the depth it came from. */
#define FRAME_SITES 32
#define FRAME_BASE_SIZE 1000
#define FRAME_TABLE_MAX 4096

static volatile size_t frame_sink;
/* `*ret` receives this frame's return address, which the captured stack of the
   allocation below must contain one frame above the leaf's own PC. */
static DHAT_TEST_NOINLINE void* alloc_at_depth(int depth, size_t size, void** ret) {
  void* p;
  if (depth == 0) { *ret = DHAT_TEST_RETURN_ADDRESS(); p = mi_malloc(size); }
  else p = alloc_at_depth(depth - 1, size, ret);
  frame_sink += (size_t)depth;  /* keeps the recursive call out of tail position */
  return p;
}

static char* read_file(const char* path) {
  FILE* f = fopen(path, "rb"); assert(f != NULL);
  assert(fseek(f, 0, SEEK_END) == 0);
  const long len = ftell(f); assert(len > 0);
  assert(fseek(f, 0, SEEK_SET) == 0);
  char* text = (char*)mi_malloc((size_t)len + 1); assert(text != NULL);
  assert(fread(text, 1, (size_t)len, f) == (size_t)len);
  fclose(f); text[len] = 0;
  return text;
}

static bool ftbl_contains(const uintptr_t* ftbl, size_t n, uintptr_t pc) {
  for (size_t i = 0; i < n; i++) if (ftbl[i] == pc) return true;
  return false;
}

static void check_frame_table(void) {
  void* ret[FRAME_SITES]; void* blocks[FRAME_SITES];
  assert(mi_dhat_start());
  for (int d = 0; d < FRAME_SITES; d++) {
    blocks[d] = alloc_at_depth(d, FRAME_BASE_SIZE + (size_t)d, &ret[d]);
    assert(blocks[d] != NULL);
  }
  mi_dhat_stop();
  for (int d = 0; d < FRAME_SITES; d++) mi_free(blocks[d]);
  assert(mi_dhat_dump(DHAT_TEST_FILE("frames")));
  char* json = read_file(DHAT_TEST_FILE("frames"));
  assert(remove(DHAT_TEST_FILE("frames")) == 0);

  static uintptr_t ftbl[FRAME_TABLE_MAX];
  size_t nftbl = 0;
  const char* const ftbl_at = strstr(json, "\"ftbl\": ["); assert(ftbl_at != NULL);
  const char* const ftbl_end = strchr(ftbl_at, ']'); assert(ftbl_end != NULL);
  for (const char* s = strstr(ftbl_at, "\"0x"); s != NULL && s < ftbl_end; s = strstr(s + 1, "\"0x")) {
    assert(nftbl < FRAME_TABLE_MAX);
    const uintptr_t pc = (uintptr_t)strtoull(s + 1, NULL, 16);
    assert(!ftbl_contains(ftbl, nftbl, pc));  /* each distinct PC exactly once */
    ftbl[nftbl++] = pc;
  }
  assert(nftbl > 0);

  const char* const pps_at = strstr(json, "\"pps\": ["); assert(pps_at != NULL && pps_at < ftbl_at);
  size_t next_new = 0, points = 0, checked = 0;
  for (const char* s = strstr(pps_at, "{\"tb\": "); s != NULL && s < ftbl_at; s = strstr(s + 1, "{\"tb\": ")) {
    const unsigned long long tb = strtoull(s + 7, NULL, 10);
    const char* t = strstr(s, "\"fs\": ["); assert(t != NULL && t < ftbl_at);
    t += 7;
    const char* const fs_end = strchr(t, ']'); assert(fs_end != NULL && fs_end < ftbl_at);
    const bool known = (tb >= FRAME_BASE_SIZE && tb < FRAME_BASE_SIZE + FRAME_SITES);
    const uintptr_t want = (known ? (uintptr_t)ret[(size_t)(tb - FRAME_BASE_SIZE)] : 0);
    bool found = false;
    while (t < fs_end) {
      char* e; const unsigned long long idx = strtoull(t, &e, 10);
      if (e == t) break;
      t = e; while (*t == ',' || *t == ' ') t++;
      if (idx >= nftbl) {
        fprintf(stderr, "fs index %llu is past the end of a %zu-entry ftbl\n", idx, nftbl);
        assert(idx < nftbl);
      }
      /* ftbl lists PCs in first-occurrence order, so a new PC takes the next index. */
      if (idx == next_new) next_new++;
      else assert(idx < next_new);
      if (ftbl[idx] == want) found = true;
    }
    /* Checked only where the capture saw the site at all (frame-pointer walks can be cut
       short), but then the mapped stack must contain it. */
    if (known && ftbl_contains(ftbl, nftbl, want)) { assert(found); checked++; }
    points++;
  }
  /* The depths stay distinct program points only if the capture walks every frame: always
     on Windows (unwind tables) and Apple (frame-pointer ABI), elsewhere only with frame
     pointers, which CMake adds with MI_PPROF. Every CI row that runs this has them. */
  #if defined(_WIN32) || defined(__APPLE__) || MI_PPROF
  assert(points >= FRAME_SITES / 2);  /* stacks cut at DHAT_STACK_MAX could merge; these are shorter */
  assert(checked > 0);                 /* ...and the return-address check is not vacuous */
  #endif
  assert(next_new == nftbl);           /* every ftbl entry is referenced */
  printf("frame table: %zu program points, %zu distinct PCs, %zu sites checked by return address\n", points, nftbl, checked);
  mi_free(json);
}

/* ---- budget: every allocation is either recorded or counted as dropped ---------------
   On a 64-bit target, 65536..131071 bytes let the first 32 KiB table fit and not the
   second. The other values cover refusing everything, refusing later and no limit. */
#define DROP_ALLOCS 1000

static void check_every_skipped_event_is_dropped(void) {
  static const char* const budgets[] = { "1", "65536", "70000", "100000", "131072", "0" };
  for (size_t b = 0; b < sizeof(budgets) / sizeof(budgets[0]); b++) {
    test_setenv("MIMALLOC_DHAT_MAX_BYTES", budgets[b]);
    assert(mi_dhat_start());
    for (int i = 0; i < DROP_ALLOCS; i++) { void* p = mi_malloc(16); assert(p != NULL); mi_free(p); }
    mi_dhat_stats_t_decl(st);
    assert(mi_dhat_stats_get(&st));
    mi_dhat_stop();
    if (st.total_blocks + st.dropped != DROP_ALLOCS) {
      fprintf(stderr, "budget %s: %llu recorded + %llu dropped != %d allocations\n", budgets[b],
              (unsigned long long)st.total_blocks, (unsigned long long)st.dropped, DROP_ALLOCS);
      assert(st.total_blocks + st.dropped == DROP_ALLOCS);
    }
    assert(st.incomplete == (st.dropped != 0));
  }
  test_unsetenv("MIMALLOC_DHAT_MAX_BYTES");
}

#if DHAT_TEST_FORK
/* ---- fork: a parent thread's in-flight event must not wedge the child ----------------
   The parked thread holds an armed DHAT event (inflight 1) inside its ALLOCATE callback
   while the process forks; it does not exist in the child, so its event never finishes
   there. */
#define FORK_CHILD_TIMEOUT_SECS 10
#define PARK_SIZE 40       /* the one allocation the parked thread's callback parks in */
#define SELF_FORK_SIZE 48  /* the one allocation whose callback forks */

static volatile int park_armed = 0, parked = 0, park_release = 0;
static pthread_t park_thread;
static volatile int self_fork_armed = 0;
static pthread_t self_fork_thread;
static volatile pid_t self_fork_pid = -1;

/* Each branch matches its thread AND its request size, so an allocation made elsewhere
   (thread start-up, the scavenger, libc) never parks or forks. */
static void fork_on_alloc(const mi_memory_change_t* change, void* arg) {
  (void)arg;
  if (park_armed && change->request_size == PARK_SIZE && pthread_equal(pthread_self(), park_thread)) {
    park_armed = 0; parked = 1;
    while (!park_release) sched_yield();
  }
  else if (self_fork_armed && change->request_size == SELF_FORK_SIZE && pthread_equal(pthread_self(), self_fork_thread)) {
    self_fork_armed = 0;
    const pid_t pid = fork();
    if (pid == 0) alarm(FORK_CHILD_TIMEOUT_SECS);  /* covers the child's own finish_event too */
    self_fork_pid = pid;
  }
}
static void* park_main(void* arg) {
  (void)arg;
  /* Thread start-up allocates (and may start the scavenger thread); finish it first so
     the park happens in exactly the allocation below. */
  mi_free(mi_malloc(1));
  park_thread = pthread_self(); park_armed = 1;
  void* p = mi_malloc(PARK_SIZE); assert(p != NULL);  /* parks in fork_on_alloc */
  mi_free(p);
  return NULL;
}
static bool is_parked(void) { return parked != 0; }
static bool stop_is_draining(void) { return !mi_dhat_is_enabled(); }
/* Bounded, so a callback that never matches fails the test instead of hanging it. */
static void wait_for(bool (*done)(void), const char* what) {
  const time_t deadline = time(NULL) + FORK_CHILD_TIMEOUT_SECS;
  while (!done()) {
    if (time(NULL) > deadline) { fprintf(stderr, "timed out waiting until %s\n", what); abort(); }
    sched_yield();
  }
}
static void* stopper_main(void* arg) { (void)arg; mi_dhat_stop(); return NULL; }

/* Runs in the child; the exit code names the first failed step. A hang in mi_dhat_stop
   ends in SIGALRM instead. */
static int fork_child_restarts(void) {
  alarm(FORK_CHILD_TIMEOUT_SECS);
  mi_dhat_stop();
  if (!mi_dhat_start()) return 2;
  void* p = mi_malloc(24); if (p == NULL) return 3;
  mi_free(p);
  mi_dhat_stats_t_decl(st);
  if (!mi_dhat_stats_get(&st) || st.total_blocks != 1) return 4;
  mi_dhat_stop();
  return 0;
}
static void wait_fork_child(pid_t pid, const char* what) {
  int status = 0;
  assert(waitpid(pid, &status, 0) == pid);
  if (WIFSIGNALED(status)) fprintf(stderr, "%s: child killed by signal %d (mi_dhat_stop spun forever?)\n", what, WTERMSIG(status));
  else if (WEXITSTATUS(status) != 0) fprintf(stderr, "%s: child failed at step %d (2 = mi_dhat_start refused)\n", what, WEXITSTATUS(status));
  assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
}

static void install_fork_callbacks(void) {
  mi_memory_callbacks_t cbs;
  memset(&cbs, 0, sizeof(cbs));
  cbs.handlers[MI_MEMORY_ALLOCATE] = fork_on_alloc;
  assert(mi_memory_set_callbacks(&cbs));
}

/* with_stopper: another thread is also inside mi_dhat_stop, waiting for the parked
   event, so the child inherits dhat_stopping == 1 as well. */
static void check_fork_with_parked_event(bool with_stopper) {
  const char* const what = (with_stopper ? "fork during a draining stop" : "fork with a parked event");
  install_fork_callbacks();
  park_release = 0; parked = 0;
  assert(mi_dhat_start());
  pthread_t parker, stopper;
  memset(&stopper, 0, sizeof(stopper));
  assert(pthread_create(&parker, NULL, park_main, NULL) == 0);
  wait_for(is_parked, "the parked thread is inside its armed event");
  if (with_stopper) {
    assert(pthread_create(&stopper, NULL, stopper_main, NULL) == 0);
    wait_for(stop_is_draining, "the stopper is draining");
  }
  const pid_t pid = fork(); assert(pid >= 0);
  if (pid == 0) _exit(fork_child_restarts());
  park_release = 1;
  assert(pthread_join(parker, NULL) == 0);
  if (with_stopper) assert(pthread_join(stopper, NULL) == 0);
  else mi_dhat_stop();
  wait_fork_child(pid, what);
}

/* The forking thread's own armed event survives the fork and still finishes in the
   child, so the child must keep that contribution rather than zero it. */
static void check_fork_inside_own_event(void) {
  install_fork_callbacks();
  assert(mi_dhat_start());
  self_fork_pid = -1; self_fork_thread = pthread_self(); self_fork_armed = 1;
  void* p = mi_malloc(SELF_FORK_SIZE); assert(p != NULL);  /* fork_on_alloc forks mid-event */
  const pid_t pid = self_fork_pid; assert(pid >= 0);
  mi_free(p);
  if (pid == 0) _exit(fork_child_restarts());
  mi_dhat_stop();
  wait_fork_child(pid, "fork inside the thread's own event");
}
#endif

int main(int argc, char** argv) {
  if (argc > 1 && strcmp(argv[1], "--env-enabled-check") == 0) return run_env_enabled_check();
  callback_counts_t callbacks = { 0, 0, 0 };
  #if MI_MEMEVT
  assert(mi_memory_tracking_set_enabled(true));
  install_callbacks(&callbacks);
  #else
  assert_memevt_is_stubbed();
  #endif
  assert(mi_dhat_start());
  /* Empty and budget-exhausted sessions still need a valid, fail-soft JSON dump. */
  assert(mi_dhat_dump(DHAT_TEST_FILE("empty")));

  void* p = mi_malloc(16); assert(p != NULL);
  void* q = mi_malloc(32); assert(q != NULL);
  p = mi_realloc(p, 20); assert(p != NULL); /* exact identity whether this stays put or moves */
  mi_free(q);

  mi_dhat_stats_t_decl(mid);
  assert(mi_dhat_stats_get(&mid));
  assert(mid.enabled && !mid.incomplete);
  /* realloc is a second allocation call for DHAT totals while retaining p's
     identity/lifetime, so the 20-byte request adds one block and 20 bytes. */
  assert(mid.total_blocks == 3 && mid.total_bytes == 68);
  assert(mid.live_blocks == 1 && mid.live_bytes == 20);
  assert(mid.peak_bytes >= 48 && mid.peak_bytes >= mid.live_bytes);
  #if MI_MEMEVT
  assert(callbacks.alloc == 2 && callbacks.free == 1 && callbacks.resize == 1);
  #endif
  /* Dump while active: stdio itself may allocate, so this also verifies dump-time
     recursion suppression and that serialization never re-enters its own lock. */
  const callback_counts_t callbacks_before_dump = callbacks;
  assert(mi_dhat_dump(DHAT_TEST_FILE("output")));
  assert(memcmp(&callbacks, &callbacks_before_dump, sizeof(callbacks)) == 0);

  mi_free(p);

  /* Force the over-aligned fallback, then exercise its in-place resize. DHAT
     must report caller requests (16 and 12), never the internal over-allocation. */
  void* aligned = mi_malloc_aligned(16, 64); assert(aligned != NULL);
  aligned = mi_realloc_aligned(aligned, 12, 64); assert(aligned != NULL);
  mi_free(aligned);

  mi_dhat_stats_t_decl(done);
  assert(mi_dhat_stats_get(&done));
  assert(done.live_blocks == 0 && done.live_bytes == 0);
  assert(done.total_blocks == 5 && done.total_bytes == 96);

  mi_dhat_stop();
  assert(!mi_dhat_is_enabled());
  assert(remove(DHAT_TEST_FILE("empty")) == 0);
  assert(mi_dhat_dump(DHAT_TEST_FILE("output")));
  FILE* f = fopen(DHAT_TEST_FILE("output"), "rb"); assert(f != NULL);
  char json[8192]; const size_t n = fread(json, 1, sizeof(json) - 1, f); fclose(f); json[n] = 0;
  assert(strstr(json, "\"dhatFileVersion\": 2") != NULL);
  assert(strstr(json, "\"bklt\": true") != NULL);
  assert(strstr(json, "\"bkacc\": false") != NULL);
  assert(strstr(json, "\"pps\"") != NULL && strstr(json, "\"ftbl\"") != NULL);
  assert(remove(DHAT_TEST_FILE("output")) == 0);

  check_frame_table();
  check_every_skipped_event_is_dropped();
  #if DHAT_TEST_FORK
  check_fork_with_parked_event(false);
  check_fork_with_parked_event(true);
  check_fork_inside_own_event();
  #endif
  #if MI_MEMEVT
  assert(mi_memory_set_callbacks(NULL));
  #endif
  puts("DHAT tests passed");
  return 0;
}

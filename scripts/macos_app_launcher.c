#include <errno.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

static volatile sig_atomic_t child_pid = -1;

static void forward_signal(int sig) {
    if (child_pid > 0) {
        kill((pid_t)child_pid, sig);
    }
}

int main(int argc, char **argv) {
    char exe_path[PATH_MAX];
    uint32_t size = sizeof(exe_path);
    if (_NSGetExecutablePath(exe_path, &size) != 0) {
        return 126;
    }

    char *last_slash = strrchr(exe_path, '/');
    if (last_slash == NULL) {
        return 126;
    }
    *last_slash = '\0';

    char script_path[PATH_MAX];
    int written = snprintf(script_path, sizeof(script_path), "%s/../Resources/shellframe.sh", exe_path);
    if (written < 0 || written >= (int)sizeof(script_path)) {
        return 126;
    }

    char **child_argv = calloc((size_t)argc + 2, sizeof(char *));
    if (child_argv == NULL) {
        return 126;
    }

    child_argv[0] = "/bin/bash";
    child_argv[1] = script_path;
    for (int i = 1; i < argc; i++) {
        child_argv[i + 1] = argv[i];
    }
    child_argv[argc + 1] = NULL;

    signal(SIGINT, forward_signal);
    signal(SIGTERM, forward_signal);
    signal(SIGHUP, forward_signal);
    signal(SIGQUIT, forward_signal);

    pid_t pid = 0;
    int rc = posix_spawn(&pid, "/bin/bash", NULL, NULL, child_argv, environ);
    if (rc != 0) {
        errno = rc;
        perror("posix_spawn");
        free(child_argv);
        return 126;
    }
    child_pid = pid;
    free(child_argv);

    int status = 0;
    while (waitpid(pid, &status, 0) < 0) {
        if (errno == EINTR) {
            continue;
        }
        perror("waitpid");
        return 126;
    }

    child_pid = -1;
    if (WIFEXITED(status)) {
        return WEXITSTATUS(status);
    }
    if (WIFSIGNALED(status)) {
        return 128 + WTERMSIG(status);
    }
    return 126;
}

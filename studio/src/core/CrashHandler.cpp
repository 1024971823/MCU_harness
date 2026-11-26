#include "core/CrashHandler.h"

#include <QDateTime>
#include <QDir>
#include <QStandardPaths>

#include <cstdio>
#include <cstring>

#ifdef Q_OS_WIN
#include <windows.h>
#include <dbghelp.h>
#else
#include <csignal>
#include <execinfo.h>
#include <fcntl.h>
#include <unistd.h>
#endif

namespace {

char g_crashDir[1024] = {0};
char g_version[64] = "unknown";
char g_gitHash[64] = "unknown";

void copyToBuf(char *dst, size_t dstSize, const QByteArray &src) {
    if (dstSize == 0) {
        return;
    }
    const size_t n = qMin(static_cast<size_t>(src.size()), dstSize - 1);
    std::memcpy(dst, src.constData(), n);
    dst[n] = '\0';
}

QString ensureCrashDir() {
    QString base = QStandardPaths::writableLocation(QStandardPaths::AppLocalDataLocation);
    if (base.isEmpty()) {
        base = QDir::tempPath() + QStringLiteral("/mcu-harness-studio");
    }
    QDir dir(base + QStringLiteral("/crashes"));
    if (!dir.exists()) {
        dir.mkpath(QStringLiteral("."));
    }
    return dir.absolutePath();
}

#ifndef Q_OS_WIN

void writeStr(int fd, const char *s) {
    if (!s) {
        return;
    }
    size_t len = 0;
    while (s[len] != '\0') {
        ++len;
    }
    size_t off = 0;
    while (off < len) {
        ssize_t w = ::write(fd, s + off, len - off);
        if (w <= 0) {
            break;
        }
        off += static_cast<size_t>(w);
    }
}

void writeUInt64(int fd, unsigned long long val) {
    char buf[32];
    int pos = 0;
    if (val == 0) {
        buf[pos++] = '0';
    } else {
        char rev[32];
        int r = 0;
        while (val > 0) {
            rev[r++] = static_cast<char>('0' + (val % 10));
            val /= 10;
        }
        while (r > 0) {
            buf[pos++] = rev[--r];
        }
    }
    buf[pos] = '\0';
    writeStr(fd, buf);
}

const char *signalName(int sig) {
    switch (sig) {
    case SIGSEGV:
        return "SIGSEGV";
    case SIGABRT:
        return "SIGABRT";
    case SIGFPE:
        return "SIGFPE";
    case SIGILL:
        return "SIGILL";
    case SIGBUS:
        return "SIGBUS";
    default:
        return "SIGNAL";
    }
}

void buildCrashPath(char *out, size_t outSize, const char *prefix, const char *ext, unsigned long long ts, unsigned long long pid) {
    if (outSize == 0) {
        return;
    }
    out[0] = '\0';
    auto appendStr = [&](const char *s) {
        size_t cur = std::strlen(out);
        size_t slen = std::strlen(s);
        if (cur + slen >= outSize) {
            slen = (outSize > cur + 1) ? (outSize - cur - 1) : 0;
        }
        if (slen > 0) {
            std::memcpy(out + cur, s, slen);
            out[cur + slen] = '\0';
        }
    };
    auto appendNum = [&](unsigned long long v) {
        char tmp[32];
        int p = 0;
        if (v == 0) {
            tmp[p++] = '0';
        } else {
            char rev[32];
            int r = 0;
            while (v > 0) {
                rev[r++] = static_cast<char>('0' + (v % 10));
                v /= 10;
            }
            while (r > 0) {
                tmp[p++] = rev[--r];
            }
        }
        tmp[p] = '\0';
        appendStr(tmp);
    };

    appendStr(g_crashDir);
    appendStr("/");
    appendStr(prefix);
    appendNum(ts);
    appendStr("-");
    appendNum(pid);
    appendStr(ext);
}

void posixCrashHandler(int sig) {
    struct sigaction sa{};
    sa.sa_handler = SIG_DFL;
    sigemptyset(&sa.sa_mask);
    ::sigaction(sig, &sa, nullptr);

    const unsigned long long ts = static_cast<unsigned long long>(::time(nullptr));
    const unsigned long long pid = static_cast<unsigned long long>(::getpid());

    char jsonPath[1200];
    char btPath[1200];
    buildCrashPath(jsonPath, sizeof(jsonPath), "crash-", ".json", ts, pid);
    buildCrashPath(btPath, sizeof(btPath), "crash-", ".bt", ts, pid);

    // Write raw backtrace first
    int btFd = ::open(btPath, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (btFd >= 0) {
        void *frames[64];
        const int n = ::backtrace(frames, 64);
        ::backtrace_symbols_fd(frames, n, btFd);
        ::close(btFd);
    }

    // Write structured metadata JSON (async-signal-safe)
    int jFd = ::open(jsonPath, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (jFd >= 0) {
        writeStr(jFd, "{\n  \"id\": \"crash-");
        writeUInt64(jFd, ts);
        writeStr(jFd, "-");
        writeUInt64(jFd, pid);
        writeStr(jFd, "\",\n  \"timestamp\": ");
        writeUInt64(jFd, ts);
        writeStr(jFd, ",\n  \"signal\": \"");
        writeStr(jFd, signalName(sig));
        writeStr(jFd, "\",\n  \"signalCode\": ");
        writeUInt64(jFd, static_cast<unsigned long long>(sig));
        writeStr(jFd, ",\n  \"version\": \"");
        writeStr(jFd, g_version);
        writeStr(jFd, "\",\n  \"gitHash\": \"");
        writeStr(jFd, g_gitHash);
        writeStr(jFd, "\",\n  \"platform\": \"");
#ifdef Q_OS_MACOS
        writeStr(jFd, "macos");
#else
        writeStr(jFd, "linux");
#endif
        writeStr(jFd, "\",\n  \"backtraceFile\": \"");
        writeStr(jFd, btPath);
        writeStr(jFd, "\"\n}\n");
        ::close(jFd);
    }

    ::raise(sig);
    ::_exit(128 + sig);
}

#else

LONG WINAPI windowsCrashHandler(EXCEPTION_POINTERS *exInfo) {
    const unsigned long long ts = static_cast<unsigned long long>(QDateTime::currentSecsSinceEpoch());
    const unsigned long long pid = static_cast<unsigned long long>(GetCurrentProcessId());
    char jsonPath[1200];
    char dmpPath[1200];
    std::snprintf(jsonPath, sizeof(jsonPath), "%s/crash-%llu-%llu.json", g_crashDir, ts, pid);
    std::snprintf(dmpPath, sizeof(dmpPath), "%s/crash-%llu-%llu.dmp", g_crashDir, ts, pid);

    HANDLE hFile = CreateFileA(dmpPath, GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (hFile != INVALID_HANDLE_VALUE) {
        MINIDUMP_EXCEPTION_INFORMATION mdei{};
        mdei.ThreadId = GetCurrentThreadId();
        mdei.ExceptionPointers = exInfo;
        mdei.ClientPointers = FALSE;
        MiniDumpWriteDump(GetCurrentProcess(), pid, hFile, MiniDumpNormal, exInfo ? &mdei : nullptr, nullptr, nullptr);
        CloseHandle(hFile);
    }

    std::FILE *fp = std::fopen(jsonPath, "wb");
    if (fp) {
        const unsigned long code = exInfo && exInfo->ExceptionRecord ? exInfo->ExceptionRecord->ExceptionCode : 0;
        std::fprintf(fp,
                     "{\n  \"id\": \"crash-%llu-%llu\",\n  \"timestamp\": %llu,\n"
                     "  \"signal\": \"0x%08lX\",\n  \"version\": \"%s\",\n"
                     "  \"gitHash\": \"%s\",\n  \"platform\": \"windows\",\n"
                     "  \"dumpFile\": \"%s\"\n}\n",
                     ts, pid, ts, code, g_version, g_gitHash, dmpPath);
        std::fclose(fp);
    }
    return EXCEPTION_EXECUTE_HANDLER;
}

#endif

} // namespace

void CrashHandler::install(const QString &version, const QString &gitHash) {
    const QString dirPath = ensureCrashDir();
    copyToBuf(g_crashDir, sizeof(g_crashDir), dirPath.toUtf8());
    copyToBuf(g_version, sizeof(g_version), version.toUtf8());
    copyToBuf(g_gitHash, sizeof(g_gitHash), gitHash.toUtf8());

#ifdef Q_OS_WIN
    SetUnhandledExceptionFilter(windowsCrashHandler);
#else
    struct sigaction sa{};
    sa.sa_handler = posixCrashHandler;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = SA_RESETHAND;
    ::sigaction(SIGSEGV, &sa, nullptr);
    ::sigaction(SIGABRT, &sa, nullptr);
    ::sigaction(SIGFPE, &sa, nullptr);
    ::sigaction(SIGILL, &sa, nullptr);
    ::sigaction(SIGBUS, &sa, nullptr);
#endif
}

QString CrashHandler::crashDirectory() {
    return ensureCrashDir();
}

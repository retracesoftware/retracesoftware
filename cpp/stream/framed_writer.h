#pragma once

#include <cstdint>
#include <cstring>
#include <vector>
#include <cerrno>

#ifndef _WIN32
#include <unistd.h>
#endif

#ifndef PIPE_BUF
#define PIPE_BUF 512
#endif

namespace retracesoftware_stream {

inline uint16_t to_le16(uint16_t v) {
#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__
    return __builtin_bswap16(v);
#else
    return v;
#endif
}
inline uint32_t to_le32(uint32_t v) {
#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__
    return __builtin_bswap32(v);
#else
    return v;
#endif
}
inline uint64_t to_le64(uint64_t v) {
#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__
    return __builtin_bswap64(v);
#else
    return v;
#endif
}

class FramedWriter {
public:
    static constexpr size_t FRAME_HEADER_SIZE = 6;
    static constexpr size_t MAX_FRAME = 65536;

private:
    int fd_ = -1;
    size_t max_payload_;
    bool raw_ = false;
    uint8_t pid_bytes_[4];
    std::vector<uint8_t> buf_;
    uint64_t bytes_written_ = 0;

    void write_raw(const uint8_t* data, size_t len) {
        while (len > 0) {
            ssize_t written = ::write(fd_, data, len);
            if (written < 0) {
                if (errno == EINTR) continue;
                break;
            }
            data += written;
            len -= (size_t)written;
        }
    }

public:
    FramedWriter() : max_payload_(0) {}

    FramedWriter(int fd, size_t frame_size, bool raw = false)
        : fd_(fd),
          max_payload_(std::min(frame_size, MAX_FRAME) - FRAME_HEADER_SIZE),
          raw_(raw)
    {
        buf_.reserve(65536);
        stamp_pid();
    }

    int fd() const { return fd_; }

    void stamp_pid() {
        uint32_t pid = (uint32_t)::getpid();
        pid_bytes_[0] = (uint8_t)(pid);
        pid_bytes_[1] = (uint8_t)(pid >> 8);
        pid_bytes_[2] = (uint8_t)(pid >> 16);
        pid_bytes_[3] = (uint8_t)(pid >> 24);
    }

    inline void write_byte(uint8_t b) {
        buf_.push_back(b);
    }

    inline void write_uint16(uint16_t v) {
        v = to_le16(v);
        const uint8_t* p = reinterpret_cast<const uint8_t*>(&v);
        buf_.insert(buf_.end(), p, p + sizeof(v));
    }

    inline void write_uint32(uint32_t v) {
        v = to_le32(v);
        const uint8_t* p = reinterpret_cast<const uint8_t*>(&v);
        buf_.insert(buf_.end(), p, p + sizeof(v));
    }

    inline void write_uint64(uint64_t v) {
        v = to_le64(v);
        const uint8_t* p = reinterpret_cast<const uint8_t*>(&v);
        buf_.insert(buf_.end(), p, p + sizeof(v));
    }

    inline void write_int64(int64_t v) {
        write_uint64((uint64_t)v);
    }

    inline void write_float64(double d) {
        uint64_t bits;
        memcpy(&bits, &d, sizeof(bits));
        write_uint64(bits);
    }

    inline void write_bytes(const uint8_t* data, size_t len) {
        buf_.insert(buf_.end(), data, data + len);
    }

    void flush() {
        if (buf_.empty() || fd_ < 0) return;

        bytes_written_ += buf_.size();

        if (raw_) {
            write_raw(buf_.data(), buf_.size());
            buf_.clear();
            return;
        }

        const uint8_t* data = buf_.data();
        size_t remaining = buf_.size();
        uint8_t frame_header[FRAME_HEADER_SIZE];
        memcpy(frame_header, pid_bytes_, 4);

        while (remaining > 0) {
            uint16_t chunk = (uint16_t)std::min(remaining, max_payload_);
            frame_header[4] = (uint8_t)(chunk);
            frame_header[5] = (uint8_t)(chunk >> 8);
            write_raw(frame_header, FRAME_HEADER_SIZE);
            write_raw(data, chunk);
            data += chunk;
            remaining -= chunk;
        }
        buf_.clear();
    }

    void close() {
        flush();
        if (fd_ >= 0) {
            ::close(fd_);
            fd_ = -1;
        }
    }

    bool is_closed() const { return fd_ < 0; }

    size_t buffered() const { return buf_.size(); }
    uint64_t bytes_written() const { return bytes_written_; }
};

}

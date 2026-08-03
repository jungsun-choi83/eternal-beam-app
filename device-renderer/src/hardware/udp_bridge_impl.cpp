#include "udp_bridge_impl.h"

#include <array>
#include <cstdio>
#include <cstring>
#include <utility>

#include <nlohmann/json.hpp>

#if defined(_WIN32)
#include <winsock2.h>
#include <ws2tcpip.h>
using SocketType = SOCKET;
constexpr SocketType kInvalidSocket = INVALID_SOCKET;
#else
#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
using SocketType = int;
constexpr SocketType kInvalidSocket = -1;
#endif

namespace eb::hardware {

namespace {

SocketType ToSocket(std::intptr_t handle) { return static_cast<SocketType>(handle); }
std::intptr_t FromSocket(SocketType sock) { return static_cast<std::intptr_t>(sock); }

std::string JsonScalarToString(const nlohmann::json &value) {
  if (value.is_string()) return value.get<std::string>();
  if (value.is_number_integer()) return std::to_string(value.get<long long>());
  if (value.is_number_unsigned()) return std::to_string(value.get<unsigned long long>());
  if (value.is_number_float()) return std::to_string(value.get<double>());
  return "";
}

std::string FirstPresent(const nlohmann::json &doc, std::initializer_list<const char *> keys) {
  for (const char *key : keys) {
    if (doc.contains(key)) {
      return JsonScalarToString(doc[key]);
    }
  }
  return "";
}

}  // namespace

std::optional<SensorEvent> ParseUdpSensorEvent(const std::string &line) {
  nlohmann::json doc;
  try {
    doc = nlohmann::json::parse(line);
  } catch (const nlohmann::json::parse_error &) {
    return std::nullopt;
  }
  if (!doc.is_object() || !doc.contains("event")) {
    return std::nullopt;
  }

  const std::string event = doc.value("event", "");

  if (event == "touch") {
    return SensorEvent{ActionEvent::Touch, FirstPresent(doc, {"distance_mm"})};
  }
  if (event == "voice") {
    return SensorEvent{ActionEvent::Voice, FirstPresent(doc, {"rms", "source"})};
  }
  if (event == "nfc_tagged") {
    return SensorEvent{ActionEvent::Nfc, FirstPresent(doc, {"uid", "theme_id"})};
  }
  if (event == "idle") {
    return SensorEvent{ActionEvent::Idle, FirstPresent(doc, {"source"})};
  }
  // "approach"(근접 사전신호), "nfc_match"(재태깅/변화없음), 그 외 알려지지 않은
  // 이벤트: AppController가 이해하는 4개 액션에 대응되지 않으므로 무시한다 —
  // 누락이 아니라 의도된 동작 (see class comment in udp_bridge_impl.h).
  return std::nullopt;
}

UdpBridgeHardware::UdpBridgeHardware(HardwareConfig config) : config_(std::move(config)) {}

UdpBridgeHardware::~UdpBridgeHardware() { Shutdown(); }

bool UdpBridgeHardware::Initialize() {
  bind_host_ = config_.udp_bind_host();
  port_ = config_.udp_port();

#if defined(_WIN32)
  WSADATA wsa_data;
  if (WSAStartup(MAKEWORD(2, 2), &wsa_data) != 0) {
    std::fprintf(stderr, "[hardware:udp_bridge] WSAStartup 실패\n");
    return false;
  }
#endif

  const SocketType sock = ::socket(AF_INET, SOCK_DGRAM, 0);
  if (sock == kInvalidSocket) {
    std::fprintf(stderr, "[hardware:udp_bridge] socket() 실패\n");
    return false;
  }

  sockaddr_in addr{};
  addr.sin_family = AF_INET;
  addr.sin_port = htons(static_cast<std::uint16_t>(port_));
  addr.sin_addr.s_addr = (bind_host_.empty() || bind_host_ == "0.0.0.0")
                              ? htonl(INADDR_ANY)
                              : inet_addr(bind_host_.c_str());

  if (::bind(sock, reinterpret_cast<sockaddr *>(&addr), sizeof(addr)) != 0) {
    std::fprintf(stderr, "[hardware:udp_bridge] bind(%s:%d) 실패 — 포트가 이미 사용 중인지 확인하세요\n",
                 bind_host_.c_str(), port_);
#if defined(_WIN32)
    closesocket(sock);
    WSACleanup();
#else
    ::close(sock);
#endif
    return false;
  }

#if defined(_WIN32)
  u_long mode = 1;  // non-blocking
  ioctlsocket(sock, FIONBIO, &mode);
#else
  const int flags = fcntl(sock, F_GETFL, 0);
  fcntl(sock, F_SETFL, flags | O_NONBLOCK);
#endif

  socket_handle_ = FromSocket(sock);
  std::fprintf(stderr,
               "[hardware:udp_bridge] %s:%d 에서 대기 중 — 기존 Python 센서 브릿지가 그대로 여기로 보냅니다\n",
               bind_host_.c_str(), port_);
  return true;
}

void UdpBridgeHardware::Shutdown() {
  if (socket_handle_ < 0) {
    return;
  }
  const SocketType sock = ToSocket(socket_handle_);
#if defined(_WIN32)
  closesocket(sock);
  WSACleanup();
#else
  ::close(sock);
#endif
  socket_handle_ = -1;
}

void UdpBridgeHardware::Poll() {
  if (socket_handle_ < 0) {
    return;
  }
  const SocketType sock = ToSocket(socket_handle_);

  std::array<char, 2048> buffer{};
  for (;;) {
    const auto received = ::recvfrom(sock, buffer.data(), static_cast<int>(buffer.size()) - 1, 0, nullptr, nullptr);
    if (received <= 0) {
      break;  // EWOULDBLOCK/EAGAIN (Linux) or WSAEWOULDBLOCK (Windows) — nothing more queued right now.
    }
    buffer[static_cast<std::size_t>(received)] = '\0';

    const auto event = ParseUdpSensorEvent(std::string(buffer.data(), static_cast<std::size_t>(received)));
    if (event.has_value()) {
      Emit(*event);
    }
  }
}

void UdpBridgeHardware::SetStatusLed(bool on) {
  if (on != status_led_on_) {
    status_led_on_ = on;
    std::fprintf(stderr, "[hardware:udp_bridge] status_led -> %s (no GPIO — 로그만 출력)\n", on ? "ON" : "OFF");
  }
}

}  // namespace eb::hardware

import Foundation
import ImageIO
import CoreGraphics

/// Servidor TCP: espera a la PC (que llega por el cable USB o por Wi-Fi),
/// recibe imágenes JPEG y manda de vuelta los toques de la pantalla.
final class FrameServer {
    var onFrame: ((CGImage) -> Void)?
    var onState: ((Bool) -> Void)?

    private let port: UInt16
    private var running = false
    private var clientFD: Int32 = -1
    private let lock = NSLock()
    private let decodeQueue = DispatchQueue(label: "ipad.decode", qos: .userInteractive)
    private var busy = false

    init(port: UInt16) {
        self.port = port
    }

    func start() {
        guard !running else { return }
        running = true
        DispatchQueue.global(qos: .userInteractive).async { [weak self] in
            self?.acceptLoop()
        }
    }

    // MARK: Aceptar conexiones
    private func acceptLoop() {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { return }
        var yes: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &yes, socklen_t(MemoryLayout<Int32>.size))
        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = port.bigEndian
        addr.sin_addr.s_addr = 0
        let b = withUnsafePointer(to: &addr) { p -> Int32 in
            return p.withMemoryRebound(to: sockaddr.self, capacity: 1) { sp -> Int32 in
                return bind(fd, sp, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard b == 0, listen(fd, 1) == 0 else {
            Darwin.close(fd)
            return
        }
        while running {
            let c = accept(fd, nil, nil)
            if c < 0 {
                usleep(100000)
                continue
            }
            handleClient(c)
        }
    }

    private func handleClient(_ c: Int32) {
        var one: Int32 = 1
        setsockopt(c, IPPROTO_TCP, TCP_NODELAY, &one, socklen_t(MemoryLayout<Int32>.size))
        setsockopt(c, SOL_SOCKET, SO_NOSIGPIPE, &one, socklen_t(MemoryLayout<Int32>.size))
        lock.lock()
        clientFD = c
        lock.unlock()
        DispatchQueue.main.async { self.onState?(true) }

        var header = [UInt8](repeating: 0, count: 4)
        var payload = [UInt8](repeating: 0, count: 1 << 20)
        while running {
            if !readExact(c, &header, 4) { break }
            let len = (Int(header[0]) << 24) | (Int(header[1]) << 16) | (Int(header[2]) << 8) | Int(header[3])
            if len <= 0 || len > 16_000_000 { break }
            if payload.count < len {
                payload = [UInt8](repeating: 0, count: len)
            }
            if !readExact(c, &payload, len) { break }
            deliver(Data(payload[0..<len]))
        }

        lock.lock()
        clientFD = -1
        lock.unlock()
        Darwin.close(c)
        DispatchQueue.main.async { self.onState?(false) }
    }

    private func readExact(_ fd: Int32, _ buf: inout [UInt8], _ n: Int) -> Bool {
        var got = 0
        while got < n {
            let r = buf.withUnsafeMutableBytes { (raw: UnsafeMutableRawBufferPointer) -> Int in
                return recv(fd, raw.baseAddress! + got, n - got, 0)
            }
            if r <= 0 { return false }
            got += r
        }
        return true
    }

    // MARK: Imágenes
    private func deliver(_ jpeg: Data) {
        lock.lock()
        let skip = busy
        if !skip { busy = true }
        lock.unlock()
        if skip { return }   // si todavía estamos decodificando, descartamos: siempre la más nueva
        decodeQueue.async { [weak self] in
            guard let self = self else { return }
            let opts: [CFString: Any] = [kCGImageSourceShouldCacheImmediately: true]
            var out: CGImage? = nil
            if let src = CGImageSourceCreateWithData(jpeg as CFData, nil) {
                out = CGImageSourceCreateImageAtIndex(src, 0, opts as CFDictionary)
            }
            self.lock.lock()
            self.busy = false
            self.lock.unlock()
            if let img = out {
                DispatchQueue.main.async { self.onFrame?(img) }
            }
        }
    }

    // MARK: Enviar toques a la PC
    func sendText(_ text: String) {
        lock.lock()
        let fd = clientFD
        lock.unlock()
        guard fd >= 0, let data = text.data(using: .utf8) else { return }
        let count = data.count
        data.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            _ = Darwin.send(fd, raw.baseAddress, count, 0)
        }
    }
}

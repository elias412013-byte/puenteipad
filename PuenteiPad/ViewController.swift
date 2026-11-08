import UIKit

class ViewController: UIViewController {

    private let server = FrameServer(port: 7777)
    private let imageLayer = CALayer()
    private let statusLabel = UILabel()
    private var imageAspect: CGFloat = 4.0 / 3.0
    private var touchIDs: [ObjectIdentifier: Int] = [:]
    private var freeIDs: [Int] = Array(0..<10)
    private var gotFrame = false

    override var prefersStatusBarHidden: Bool { return true }
    override var supportedInterfaceOrientations: UIInterfaceOrientationMask { return .landscape }
    override var shouldAutorotate: Bool { return true }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        view.isMultipleTouchEnabled = true

        imageLayer.contentsGravity = .resizeAspect
        imageLayer.magnificationFilter = .linear
        imageLayer.backgroundColor = UIColor.black.cgColor
        view.layer.addSublayer(imageLayer)

        statusLabel.numberOfLines = 0
        statusLabel.textAlignment = .center
        statusLabel.textColor = .white
        statusLabel.font = UIFont.systemFont(ofSize: 24)
        statusLabel.frame = view.bounds
        statusLabel.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        view.addSubview(statusLabel)
        showWaiting()

        server.onFrame = { [weak self] cg in self?.show(cg) }
        server.onState = { [weak self] on in self?.stateChanged(on) }
        server.start()
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        imageLayer.frame = view.bounds
        CATransaction.commit()
    }

    private func showWaiting() {
        statusLabel.text = "PuenteiPad\n\nEsperando a la PC…\nConectá el cable USB, tocá «Confiar» si lo pide\ny abrí puente_ipad.py en la PC"
        statusLabel.isHidden = false
    }

    private func stateChanged(_ connected: Bool) {
        if connected {
            statusLabel.text = "Conectado ✓\nEsperando imagen…"
            statusLabel.isHidden = false
        } else {
            gotFrame = false
            CATransaction.begin()
            CATransaction.setDisableActions(true)
            imageLayer.contents = nil
            CATransaction.commit()
            showWaiting()
        }
    }

    private func show(_ cg: CGImage) {
        if !gotFrame {
            gotFrame = true
            statusLabel.isHidden = true
        }
        if cg.height > 0 {
            imageAspect = CGFloat(cg.width) / CGFloat(cg.height)
        }
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        imageLayer.contents = cg
        CATransaction.commit()
    }

    // MARK: Táctil -> PC
    /// Zona de la pantalla donde se dibuja la imagen (puede haber barras negras)
    private func imageRect() -> CGRect {
        let b = view.bounds
        var w = b.width
        var h = b.width / imageAspect
        if h > b.height {
            h = b.height
            w = h * imageAspect
        }
        return CGRect(x: (b.width - w) / 2, y: (b.height - h) / 2, width: w, height: h)
    }

    private func idFor(_ t: UITouch) -> Int {
        let key = ObjectIdentifier(t)
        if let i = touchIDs[key] { return i }
        let i = freeIDs.isEmpty ? 0 : freeIDs.removeFirst()
        touchIDs[key] = i
        return i
    }

    private func releaseID(_ t: UITouch) {
        let key = ObjectIdentifier(t)
        if let i = touchIDs[key] {
            touchIDs[key] = nil
            freeIDs.append(i)
        }
    }

    private func sendTouch(_ phase: String, _ t: UITouch) {
        let r = imageRect()
        guard r.width > 1, r.height > 1 else { return }
        let p = t.location(in: view)
        let nx = (p.x - r.minX) / r.width
        let ny = (p.y - r.minY) / r.height
        let line = "T,\(phase),\(idFor(t))," + String(format: "%.5f,%.5f", Double(nx), Double(ny)) + "\n"
        server.sendText(line)
    }

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent?) {
        for t in touches { sendTouch("D", t) }
    }

    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent?) {
        for t in touches { sendTouch("M", t) }
    }

    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent?) {
        for t in touches {
            sendTouch("U", t)
            releaseID(t)
        }
    }

    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent?) {
        for t in touches {
            sendTouch("U", t)
            releaseID(t)
        }
    }
}

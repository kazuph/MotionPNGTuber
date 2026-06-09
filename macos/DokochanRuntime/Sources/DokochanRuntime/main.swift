import AppKit
import AVFoundation
import CoreImage
import CoreMedia
import QuartzCore
import ScreenCaptureKit

let emotions = ["joy", "anger", "sad", "surprise"]
let emotionLabels = ["joy": "喜", "anger": "怒", "sad": "哀", "surprise": "驚"]
let mouthStates = ["closed", "small", "half", "open", "wide", "e", "u"]

struct TrackData: Decodable {
    let emotion: String
    let fps: Double
    let width: Double
    let height: Double
    let valid: [Int]
    let quad: [[[Double]]]
}

struct TTSWave {
    let samples: [Float]
    let sampleRate: Double
}

final class TTSController: NSObject, AVAudioPlayerDelegate {
    private var player: AVAudioPlayer?
    private(set) var wave: TTSWave?

    var isPlaying: Bool { player?.isPlaying ?? false }
    var currentTime: TimeInterval { player?.currentTime ?? 0 }

    func play(wavData: Data) throws {
        wave = try Self.decodeWave(wavData)
        player = try AVAudioPlayer(data: wavData)
        player?.delegate = self
        player?.prepareToPlay()
        player?.play()
    }

    func stop() {
        player?.stop()
        player = nil
        wave = nil
    }

    func level(at time: TimeInterval) -> (Double, Double) {
        guard let wave else { return (0, 0) }
        let center = max(0, Int(time * wave.sampleRate))
        let window = max(256, Int(wave.sampleRate * 0.030))
        let start = max(0, center - window / 2)
        let end = min(wave.samples.count, start + window)
        guard end > start else { return (0, 0) }

        var sum: Double = 0
        var diff: Double = 0
        var prev = Double(wave.samples[start])
        for i in start..<end {
            let v = Double(wave.samples[i])
            sum += v * v
            diff += abs(v - prev)
            prev = v
        }
        let rms = sqrt(sum / Double(end - start))
        let level = min(1.0, max(0.0, (rms - 0.006) / 0.18))
        let centroid = min(1.0, diff / max(0.0001, sum.squareRoot() * Double(end - start)))
        return (level, centroid)
    }

    private static func decodeWave(_ data: Data) throws -> TTSWave {
        enum WaveError: Error { case invalid }
        func u16(_ offset: Int) -> UInt16 {
            data.withUnsafeBytes { UInt16(littleEndian: $0.loadUnaligned(fromByteOffset: offset, as: UInt16.self)) }
        }
        func u32(_ offset: Int) -> UInt32 {
            data.withUnsafeBytes { UInt32(littleEndian: $0.loadUnaligned(fromByteOffset: offset, as: UInt32.self)) }
        }

        guard data.count > 44 else { throw WaveError.invalid }
        var offset = 12
        var audioFormat: UInt16 = 1
        var channels: UInt16 = 1
        var sampleRate: UInt32 = 48_000
        var bitsPerSample: UInt16 = 16
        var dataRange: Range<Int>?

        while offset + 8 <= data.count {
            let id = String(data: data[offset..<offset + 4], encoding: .ascii) ?? ""
            let size = Int(u32(offset + 4))
            let body = offset + 8
            let next = body + size + (size % 2)
            guard body + size <= data.count else { break }
            if id == "fmt " {
                audioFormat = u16(body)
                channels = u16(body + 2)
                sampleRate = u32(body + 4)
                bitsPerSample = u16(body + 14)
            } else if id == "data" {
                dataRange = body..<body + size
                break
            }
            offset = next
        }
        guard let range = dataRange, channels > 0 else { throw WaveError.invalid }

        var mono: [Float] = []
        if audioFormat == 1 && bitsPerSample == 16 {
            let frameBytes = Int(channels) * 2
            let frameCount = range.count / frameBytes
            mono.reserveCapacity(frameCount)
            for frame in 0..<frameCount {
                var sum: Float = 0
                for ch in 0..<Int(channels) {
                    let p = range.lowerBound + frame * frameBytes + ch * 2
                    let raw = data.withUnsafeBytes { Int16(littleEndian: $0.loadUnaligned(fromByteOffset: p, as: Int16.self)) }
                    sum += Float(raw) / 32768.0
                }
                mono.append(sum / Float(channels))
            }
        } else if audioFormat == 3 && bitsPerSample == 32 {
            let frameBytes = Int(channels) * 4
            let frameCount = range.count / frameBytes
            mono.reserveCapacity(frameCount)
            for frame in 0..<frameCount {
                var sum: Float = 0
                for ch in 0..<Int(channels) {
                    let p = range.lowerBound + frame * frameBytes + ch * 4
                    let bits = data.withUnsafeBytes { UInt32(littleEndian: $0.loadUnaligned(fromByteOffset: p, as: UInt32.self)) }
                    sum += Float(bitPattern: bits)
                }
                mono.append(sum / Float(channels))
            }
        } else {
            throw WaveError.invalid
        }
        return TTSWave(samples: mono, sampleRate: Double(sampleRate))
    }
}

final class SystemAudioLevelMeter: NSObject, SCStreamOutput, @unchecked Sendable {
    private let lock = NSLock()
    private let queue = DispatchQueue(label: "jp.kazuph.MotionPNGTuber.systemAudio")
    private var stream: SCStream?
    private var smoothedLevel: Double = 0
    private var smoothedCentroid: Double = 0

    var level: Double {
        lock.lock()
        defer { lock.unlock() }
        return smoothedLevel
    }

    var centroid: Double {
        lock.lock()
        defer { lock.unlock() }
        return smoothedCentroid
    }

    func start(status: @escaping @MainActor @Sendable (String) -> Void) {
        Task {
            do {
                let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
                guard let display = content.displays.first else {
                    await status("画面なし")
                    return
                }
                let config = SCStreamConfiguration()
                config.width = 2
                config.height = 2
                config.minimumFrameInterval = CMTime(value: 1, timescale: 2)
                config.capturesAudio = true
                config.excludesCurrentProcessAudio = false
                config.sampleRate = 48_000
                config.channelCount = 2

                let filter = SCContentFilter(display: display, excludingWindows: [])
                let stream = SCStream(filter: filter, configuration: config, delegate: nil)
                try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
                try await stream.startCapture()
                self.stream = stream
                await status("macOS音源 待機中")
                print("[audio] ScreenCaptureKit system audio started")
            } catch {
                await status("画面収録/音声許可が必要")
                print("[audio warn] failed to start system audio: \(error)")
            }
        }
    }

    func stop() {
        let stream = self.stream
        self.stream = nil
        stream?.stopCapture { _ in }
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, sampleBuffer.isValid else { return }
        guard let blockBuffer = CMSampleBufferGetDataBuffer(sampleBuffer) else { return }
        let length = CMBlockBufferGetDataLength(blockBuffer)
        guard length > 0 else { return }
        var data = Data(count: length)
        let status = data.withUnsafeMutableBytes { raw -> OSStatus in
            guard let dst = raw.baseAddress else { return -1 }
            return CMBlockBufferCopyDataBytes(blockBuffer, atOffset: 0, dataLength: length, destination: dst)
        }
        guard status == noErr else { return }
        consumeFloat32Interleaved(data: data)
    }

    private func consumeFloat32Interleaved(data: Data) {
        let count = data.count / MemoryLayout<Float>.stride
        guard count > 0 else { return }

        var sum: Double = 0
        var diffSum: Double = 0
        data.withUnsafeBytes { raw in
            guard let ptr = raw.bindMemory(to: Float.self).baseAddress else { return }
            var prev = Double(ptr[0])
            for i in 0..<count {
                let v = Double(ptr[i])
                sum += v * v
                diffSum += abs(v - prev)
                prev = v
            }
        }
        let rms = sqrt(sum / Double(count))
        let normalized = min(1.0, max(0.0, (rms - 0.008) / 0.16))
        let centroidLike = min(1.0, diffSum / max(0.0001, sum.squareRoot() * Double(count)))

        lock.lock()
        smoothedLevel = smoothedLevel * 0.72 + normalized * 0.28
        smoothedCentroid = smoothedCentroid * 0.80 + centroidLike * 0.20
        lock.unlock()
    }
}

final class DokochanRuntimeView: NSView {
    enum InputMode {
        case systemAudio
        case tts
    }

    private let repoRoot: URL
    private let ciContext = CIContext(options: [.workingColorSpace: NSNull()])
    private let videoView = NSImageView()
    private let mouthView = NSImageView()
    private let controlPanel = NSStackView()
    private let modeControl = NSSegmentedControl(labels: ["macOS音源", "Irodori TTS"], trackingMode: .selectOne, target: nil, action: nil)
    private let ttsField = NSTextField(string: "")
    private let ttsButton = NSButton(title: "送信", target: nil, action: nil)
    private let statusLabel = NSTextField(labelWithString: "")
    private let buttonStack = NSStackView()
    private let audio = SystemAudioLevelMeter()
    private let tts = TTSController()
    private var imageGenerators: [String: AVAssetImageGenerator] = [:]
    private var durations: [String: Double] = [:]
    private var tracks: [String: TrackData] = [:]
    private var mouths: [String: [String: NSImage]] = [:]
    private var activeEmotion = "joy"
    private var inputMode: InputMode = .systemAudio
    private var displayTimer: Timer?
    private var videoStartTime = CACurrentMediaTime()
    private var lastRenderedVideoKey = ""

    init(repoRoot: URL) {
        self.repoRoot = repoRoot
        super.init(frame: NSRect(x: 0, y: 0, width: 1280, height: 720))
        print("[app] repoRoot=\(repoRoot.path)")
        wantsLayer = true
        layer?.backgroundColor = NSColor.black.cgColor
        setupVideoView()
        setupMouthView()
        setupControls()
        videoView.frame = bounds
        layoutControls()
        loadAssets()
        switchEmotion("joy", preservePhase: false)
        audio.start { [weak self] text in
            guard let self, self.inputMode == .systemAudio else { return }
            self.statusLabel.stringValue = text
        }
        displayTimer = Timer.scheduledTimer(withTimeInterval: 1.0 / 60.0, repeats: true) { [weak self] _ in
            Task { @MainActor in
                self?.tick()
            }
        }
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func layout() {
        super.layout()
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        videoView.frame = bounds
        layoutControls()
        CATransaction.commit()
    }

    private func setupVideoView() {
        videoView.imageScaling = .scaleProportionallyUpOrDown
        videoView.wantsLayer = true
        videoView.layer?.backgroundColor = NSColor.black.cgColor
        addSubview(videoView, positioned: .below, relativeTo: nil)
    }

    private func setupMouthView() {
        mouthView.imageScaling = .scaleAxesIndependently
        mouthView.wantsLayer = true
        mouthView.layer?.masksToBounds = false
        addSubview(mouthView, positioned: .above, relativeTo: videoView)
    }

    private func setupControls() {
        controlPanel.orientation = .vertical
        controlPanel.spacing = 8
        controlPanel.edgeInsets = NSEdgeInsets(top: 10, left: 10, bottom: 10, right: 10)
        controlPanel.wantsLayer = true
        controlPanel.layer?.backgroundColor = NSColor.black.withAlphaComponent(0.72).cgColor
        controlPanel.layer?.cornerRadius = 8

        modeControl.selectedSegment = 0
        modeControl.target = self
        modeControl.action = #selector(modeChanged(_:))
        controlPanel.addArrangedSubview(modeControl)

        ttsField.placeholderString = "どこちゃんに喋らせるテキスト"
        ttsField.isHidden = true
        controlPanel.addArrangedSubview(ttsField)

        ttsButton.target = self
        ttsButton.action = #selector(sendTTS(_:))
        ttsButton.isHidden = true
        controlPanel.addArrangedSubview(ttsButton)

        statusLabel.textColor = .secondaryLabelColor
        statusLabel.font = NSFont.systemFont(ofSize: 11)
        statusLabel.lineBreakMode = .byTruncatingTail
        controlPanel.addArrangedSubview(statusLabel)

        buttonStack.orientation = .vertical
        buttonStack.spacing = 6
        controlPanel.addArrangedSubview(buttonStack)

        for emotion in emotions {
            let button = NSButton(title: "\(emotionLabels[emotion] ?? "")  \(emotion)", target: self, action: #selector(emotionButton(_:)))
            button.identifier = NSUserInterfaceItemIdentifier(emotion)
            button.bezelStyle = .rounded
            buttonStack.addArrangedSubview(button)
        }
        addSubview(controlPanel, positioned: .above, relativeTo: mouthView)
    }

    private func layoutControls() {
        let width: CGFloat = 270
        let height: CGFloat = inputMode == .tts ? 270 : 210
        controlPanel.frame = NSRect(
            x: bounds.width - width - 18,
            y: bounds.height - height - 18,
            width: width,
            height: height
        )
    }

    @objc private func emotionButton(_ sender: NSButton) {
        guard let emotion = sender.identifier?.rawValue else { return }
        switchEmotion(emotion, preservePhase: true)
    }

    @objc private func modeChanged(_ sender: NSSegmentedControl) {
        inputMode = sender.selectedSegment == 1 ? .tts : .systemAudio
        ttsField.isHidden = inputMode != .tts
        ttsButton.isHidden = inputMode != .tts
        if inputMode == .systemAudio {
            tts.stop()
            statusLabel.stringValue = ""
        }
        needsLayout = true
        print("[input] mode=\(inputMode == .tts ? "tts" : "system-audio")")
    }

    @objc private func sendTTS(_ sender: NSButton) {
        let text = ttsField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        statusLabel.stringValue = "生成中..."
        ttsButton.isEnabled = false
        synthesizeIrodori(text: text) { [weak self] result in
            DispatchQueue.main.async {
                guard let self else { return }
                self.ttsButton.isEnabled = true
                switch result {
                case .success(let data):
                    do {
                        try self.tts.play(wavData: data)
                        self.inputMode = .tts
                        self.modeControl.selectedSegment = 1
                        self.ttsField.isHidden = false
                        self.ttsButton.isHidden = false
                        self.statusLabel.stringValue = "再生中"
                        print("[tts] playback started bytes=\(data.count)")
                    } catch {
                        self.statusLabel.stringValue = "再生失敗"
                        print("[tts warn] playback failed: \(error)")
                    }
                case .failure(let error):
                    self.statusLabel.stringValue = "TTS失敗"
                    print("[tts warn] request failed: \(error)")
                }
            }
        }
    }

    private func synthesizeIrodori(text: String, completion: @escaping (Result<Data, Error>) -> Void) {
        let urlString = ProcessInfo.processInfo.environment["IRODORI_TTS_URL"] ?? "http://100.80.152.112:8088/api/tts/v1/tts"
        let voice = ProcessInfo.processInfo.environment["IRODORI_VOICE_LOCK"] ?? "5f34b71233d8450895d15e5d70318aa8"
        guard let url = URL(string: urlString) else {
            completion(.failure(NSError(domain: "DokochanRuntime", code: 1, userInfo: [NSLocalizedDescriptionKey: "invalid TTS URL"])))
            return
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 120
        request.httpBody = try? JSONSerialization.data(withJSONObject: ["text": text, "voice_lock_id": voice])
        URLSession.shared.dataTask(with: request) { data, response, error in
            if let error {
                completion(.failure(error))
                return
            }
            if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                completion(.failure(NSError(domain: "DokochanRuntime", code: http.statusCode, userInfo: [NSLocalizedDescriptionKey: "HTTP \(http.statusCode)"])))
                return
            }
            completion(.success(data ?? Data()))
        }.resume()
    }

    private func loadAssets() {
        let base = repoRoot.appendingPathComponent("assets/dokochan_vtuber/seedance_layers/composited")
        let mouthBase = repoRoot.appendingPathComponent("assets/dokochan_vtuber/mouth")
        let decoder = JSONDecoder()

        for emotion in emotions {
            let videoURL = base.appendingPathComponent("loop_\(emotion)_mouthless.mp4")
            print("[asset] video \(emotion) exists=\(FileManager.default.fileExists(atPath: videoURL.path)) path=\(videoURL.path)")
            let asset = AVAsset(url: videoURL)
            let generator = AVAssetImageGenerator(asset: asset)
            generator.appliesPreferredTrackTransform = true
            generator.requestedTimeToleranceBefore = .zero
            generator.requestedTimeToleranceAfter = .zero
            imageGenerators[emotion] = generator
            durations[emotion] = CMTimeGetSeconds(asset.duration)

            let trackURL = base.appendingPathComponent("mouth_track_\(emotion)_calibrated.json")
            do {
                let data = try Data(contentsOf: trackURL)
                tracks[emotion] = try decoder.decode(TrackData.self, from: data)
                print("[asset] track \(emotion) loaded bytes=\(data.count)")
            } catch {
                print("[track warn] failed to load \(trackURL.path): \(error)")
            }

            var set: [String: NSImage] = [:]
            for state in mouthStates {
                let url = mouthBase.appendingPathComponent(emotion).appendingPathComponent("\(state).png")
                if let image = NSImage(contentsOf: url) {
                    set[state] = image
                }
            }
            mouths[emotion] = set
        }
    }

    private func switchEmotion(_ emotion: String, preservePhase: Bool) {
        guard imageGenerators[emotion] != nil else { return }
        let phase = preservePhase ? currentPhase() : 0
        activeEmotion = emotion

        let duration = durations[emotion] ?? 0
        if duration > 0 {
            videoStartTime = CACurrentMediaTime() - phase * duration
        }
        lastRenderedVideoKey = ""
        print("[emotion] switched -> \(emotion) phase=\(String(format: "%.3f", phase))")
    }

    private func currentPhase() -> Double {
        let duration = durations[activeEmotion] ?? 0
        guard duration > 0 else { return 0 }
        let elapsed = CACurrentMediaTime() - videoStartTime
        return (elapsed / duration).truncatingRemainder(dividingBy: 1.0)
    }

    private func tick() {
        guard let track = tracks[activeEmotion] else { return }
        let duration = durations[activeEmotion] ?? 0
        let time = currentPhase() * duration
        guard time.isFinite else { return }
        let frame = Int(floor(time * track.fps)) % max(1, track.quad.count)
        renderVideoFrame(frame: frame, time: time)
        guard frame >= 0, frame < track.quad.count, frame < track.valid.count, track.valid[frame] != 0 else {
            mouthView.isHidden = true
            return
        }

        let state = chooseMouthState()
        guard let image = mouths[activeEmotion]?[state] ?? mouths[activeEmotion]?["open"] else {
            mouthView.isHidden = true
            return
        }
        guard let overlay = renderMouthOverlay(image: image, quad: track.quad[frame], track: track) else {
            mouthView.isHidden = true
            return
        }
        mouthView.isHidden = false
        mouthView.layer?.setAffineTransform(.identity)
        mouthView.frame = fittedVideoRect(videoWidth: CGFloat(track.width), videoHeight: CGFloat(track.height))
        mouthView.image = overlay
    }

    private func chooseMouthState() -> String {
        let signal = inputMode == .tts && tts.isPlaying ? tts.level(at: tts.currentTime) : (audio.level, audio.centroid)
        let level = signal.0
        let centroid = signal.1
        if level < 0.06 { return "closed" }
        if level < 0.17 { return "small" }
        if level < 0.36 { return "half" }
        if level > 0.74 { return "wide" }
        if centroid < 0.16 { return "u" }
        if centroid > 0.45 { return "e" }
        return "open"
    }

    private func fittedVideoRect(videoWidth: CGFloat, videoHeight: CGFloat) -> CGRect {
        let scale = min(bounds.width / videoWidth, bounds.height / videoHeight)
        let width = videoWidth * scale
        let height = videoHeight * scale
        return CGRect(
            x: (bounds.width - width) * 0.5,
            y: (bounds.height - height) * 0.5,
            width: width,
            height: height
        )
    }

    private func renderMouthOverlay(image: NSImage, quad: [[Double]], track: TrackData) -> NSImage? {
        guard quad.count >= 4 else { return nil }
        guard let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return nil }
        let src = CIImage(cgImage: cgImage)
        guard let filter = CIFilter(name: "CIPerspectiveTransform") else { return nil }
        filter.setValue(src, forKey: kCIInputImageKey)
        filter.setValue(CIVector(cgPoint: ciPoint(quad[0], imageHeight: track.height)), forKey: "inputTopLeft")
        filter.setValue(CIVector(cgPoint: ciPoint(quad[1], imageHeight: track.height)), forKey: "inputTopRight")
        filter.setValue(CIVector(cgPoint: ciPoint(quad[2], imageHeight: track.height)), forKey: "inputBottomRight")
        filter.setValue(CIVector(cgPoint: ciPoint(quad[3], imageHeight: track.height)), forKey: "inputBottomLeft")
        guard let transformed = filter.outputImage else { return nil }
        let extent = CGRect(x: 0, y: 0, width: track.width, height: track.height)
        guard let rendered = ciContext.createCGImage(transformed, from: extent) else { return nil }
        return NSImage(cgImage: rendered, size: NSSize(width: track.width, height: track.height))
    }

    private func ciPoint(_ item: [Double], imageHeight: Double) -> CGPoint {
        guard item.count >= 2 else { return .zero }
        return CGPoint(x: item[0], y: imageHeight - item[1])
    }

    private func renderVideoFrame(frame: Int, time: Double) {
        let key = "\(activeEmotion):\(frame)"
        guard key != lastRenderedVideoKey else { return }
        guard let generator = imageGenerators[activeEmotion] else { return }
        let cmTime = CMTime(seconds: time, preferredTimescale: 600)
        do {
            let cgImage = try generator.copyCGImage(at: cmTime, actualTime: nil)
            videoView.image = NSImage(cgImage: cgImage, size: NSSize(width: cgImage.width, height: cgImage.height))
            lastRenderedVideoKey = key
        } catch {
            print("[video warn] frame render failed emotion=\(activeEmotion) frame=\(frame): \(error)")
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var window: NSWindow?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let repoRoot = resolveRepoRoot()
        let view = DokochanRuntimeView(repoRoot: repoRoot)
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1280, height: 720),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Dokochan Swift Runtime"
        window.contentView = view
        window.center()
        window.level = .floating
        window.makeKeyAndOrderFront(nil)
        window.orderFrontRegardless()
        self.window = window
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    private func resolveRepoRoot() -> URL {
        let args = CommandLine.arguments
        if let idx = args.firstIndex(of: "--repo-root"), idx + 1 < args.count {
            return URL(fileURLWithPath: args[idx + 1], isDirectory: true)
        }
        if let envRoot = ProcessInfo.processInfo.environment["DOKOCHAN_REPO_ROOT"], !envRoot.isEmpty {
            return URL(fileURLWithPath: envRoot, isDirectory: true)
        }
        let bundleURL = Bundle.main.bundleURL
        if bundleURL.pathExtension == "app" {
            let repo = bundleURL
                .deletingLastPathComponent() // .build
                .deletingLastPathComponent() // DokochanRuntime
                .deletingLastPathComponent() // macos
                .deletingLastPathComponent() // repo
            return repo.standardized
        }
        let cwd = FileManager.default.currentDirectoryPath
        if cwd.hasSuffix("macos/DokochanRuntime") {
            return URL(fileURLWithPath: "../..", relativeTo: URL(fileURLWithPath: cwd, isDirectory: true)).standardized
        }
        return URL(fileURLWithPath: cwd, isDirectory: true)
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()

import AVFoundation
import Darwin
import Foundation

final class BufferRetainer: @unchecked Sendable {
    private let lock = NSLock()
    private var buffers: [ObjectIdentifier: AVAudioPCMBuffer] = [:]

    func hold(_ buffer: AVAudioPCMBuffer) -> ObjectIdentifier {
        let id = ObjectIdentifier(buffer)
        lock.lock()
        buffers[id] = buffer
        lock.unlock()
        return id
    }

    func release(_ id: ObjectIdentifier) {
        lock.lock()
        buffers.removeValue(forKey: id)
        lock.unlock()
    }
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}

guard CommandLine.arguments.count == 3,
      let rate = Double(CommandLine.arguments[1]),
      let channelCount = UInt32(CommandLine.arguments[2]),
      channelCount == 1 || channelCount == 2,
      let format = AVAudioFormat(commonFormat: .pcmFormatInt16,
                                 sampleRate: rate,
                                 channels: channelCount,
                                 interleaved: true) else {
    fail("usage: macos_pcm_player SAMPLE_RATE CHANNELS")
}

let engine = AVAudioEngine()
let player = AVAudioPlayerNode()
engine.attach(player)
engine.connect(player, to: engine.mainMixerNode, format: format)
player.volume = 1

do {
    try engine.start()
} catch {
    fail("Could not start macOS audio output: \(error)")
}
player.play()

let frameBytes = Int(channelCount) * MemoryLayout<Int16>.size
let framesPerBuffer = 1024
let bytesPerBuffer = frameBytes * framesPerBuffer
var remainder = Data()
var lastBuffer: AVAudioPCMBuffer?
let retainer = BufferRetainer()

func schedule(_ buffer: AVAudioPCMBuffer, finished: DispatchSemaphore? = nil) {
    let id = retainer.hold(buffer)
    player.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { _ in
        retainer.release(id)
        finished?.signal()
    }
}

func makeBuffer(_ bytes: Data) -> AVAudioPCMBuffer {
    let frames = bytes.count / frameBytes
    let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(frames))!
    buffer.frameLength = AVAudioFrameCount(frames)
    let audioBuffers = UnsafeMutableAudioBufferListPointer(buffer.mutableAudioBufferList)
    guard audioBuffers.count == 1, let destination = audioBuffers[0].mData else {
        fail("Could not allocate an interleaved PCM buffer")
    }
    bytes.withUnsafeBytes { source in
        _ = memcpy(destination, source.baseAddress!, bytes.count)
    }
    audioBuffers[0].mDataByteSize = UInt32(bytes.count)
    return buffer
}

do {
    while let chunk = try FileHandle.standardInput.read(upToCount: bytesPerBuffer), !chunk.isEmpty {
        remainder.append(chunk)
        while remainder.count >= bytesPerBuffer {
            let data = Data(remainder.prefix(bytesPerBuffer))
            remainder.removeFirst(bytesPerBuffer)
            if let previous = lastBuffer {
                schedule(previous)
            }
            lastBuffer = makeBuffer(data)
        }
    }
} catch {
    fail("Could not read PCM input: \(error)")
}

if remainder.count % frameBytes != 0 {
    fail("PCM input ended in the middle of a sample frame")
}
if !remainder.isEmpty {
    if let previous = lastBuffer {
        schedule(previous)
    }
    lastBuffer = makeBuffer(remainder)
}

if let lastBuffer {
    let finished = DispatchSemaphore(value: 0)
    schedule(lastBuffer, finished: finished)
    if finished.wait(timeout: .now() + 300) == .timedOut {
        fail("Timed out waiting for audio playback to finish")
    }
}

player.stop()
engine.stop()

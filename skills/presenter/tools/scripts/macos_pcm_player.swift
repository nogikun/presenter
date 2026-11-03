// Play s16le PCM from stdin as it arrives (presenter's --play on macOS), then exit when it is heard.
// Usage: macos_pcm_player SAMPLE_RATE CHANNELS
import AVFoundation
import Foundation

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
do {
    try engine.start()
} catch {
    fail("Could not start macOS audio output: \(error)")
}
player.play()

let frameBytes = Int(channelCount) * MemoryLayout<Int16>.size

// The player node keeps each scheduled buffer until it has played.
func buffer(_ bytes: Data) -> AVAudioPCMBuffer {
    let frames = AVAudioFrameCount(bytes.count / frameBytes)
    guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: max(frames, 1)) else {
        fail("Could not allocate a PCM buffer")
    }
    buffer.frameLength = frames
    bytes.withUnsafeBytes { source in
        if let base = source.baseAddress {
            memcpy(buffer.mutableAudioBufferList.pointee.mBuffers.mData, base, bytes.count)
        }
    }
    return buffer
}

// Schedule whatever arrived at once: holding bytes back until more come would delay the end of a
// sentence until the next one is generated. Only a partial sample frame waits.
var pending = Data()
while true {
    let chunk = FileHandle.standardInput.availableData  // Blocks for data; empty at EOF.
    if chunk.isEmpty {
        break
    }
    pending.append(chunk)
    let usable = pending.count - pending.count % frameBytes
    if usable > 0 {
        player.scheduleBuffer(buffer(pending.prefix(usable)))
        pending = Data(pending.dropFirst(usable))
    }
}
if !pending.isEmpty {
    fail("PCM input ended in the middle of a sample frame")
}

// Buffers play in order, so a last (silent, 1-frame) buffer finishing means everything was heard.
let finished = DispatchSemaphore(value: 0)
let end = buffer(Data(count: frameBytes))
player.scheduleBuffer(end, completionCallbackType: .dataPlayedBack) { _ in finished.signal() }
finished.wait()
player.stop()
engine.stop()

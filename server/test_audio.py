import wave
import struct

w = wave.open('visitor.wav', 'rb')
data = w.readframes(w.getnframes())
shorts = struct.unpack(str(len(data)//2) + 'h', data)

print(f'Max: {max(shorts)}')
print(f'Min: {min(shorts)}')
print(f'Zeros: {shorts.count(0)}')
print(f'Total: {len(shorts)}')
print(f'Ratio of Zeros: {shorts.count(0) / len(shorts):.2%}')

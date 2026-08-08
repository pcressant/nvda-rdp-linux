from client.rdaccess.framing import FrameDecoder, encode_frame


def test_complete_frame() -> None:
    assert FrameDecoder().feed(encode_frame(b"hello")) == [b"hello"]


def test_fragmented_frame() -> None:
    decoder = FrameDecoder()
    encoded = encode_frame(b"fragmented")
    assert decoder.feed(encoded[:2]) == []
    assert decoder.feed(encoded[2:7]) == []
    assert decoder.feed(encoded[7:]) == [b"fragmented"]


def test_concatenated_frames() -> None:
    encoded = encode_frame(b"one") + encode_frame(b"two")
    assert FrameDecoder().feed(encoded) == [b"one", b"two"]

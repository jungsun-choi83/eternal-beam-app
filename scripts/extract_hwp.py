import sys
import zlib
import struct
import olefile


def extract_text(path):
    f = olefile.OleFileIO(path)
    dirs = f.listdir()

    # Determine compression from FileHeader
    header = f.openstream("FileHeader").read()
    is_compressed = bool(header[36] & 1)

    sections = []
    for entry in dirs:
        if entry[0] == "BodyText":
            sections.append(entry)

    def sort_key(e):
        name = e[-1]
        try:
            return int(name.replace("Section", ""))
        except ValueError:
            return 0

    sections.sort(key=sort_key)

    out = []
    for sec in sections:
        data = f.openstream(sec).read()
        if is_compressed:
            try:
                data = zlib.decompress(data, -15)
            except zlib.error:
                pass
        out.append(parse_section(data))
    f.close()
    return "\n".join(out)


def parse_section(data):
    text_parts = []
    i = 0
    size = len(data)
    while i < size - 4:
        header = struct.unpack_from("<I", data, i)[0]
        tag_id = header & 0x3FF
        level = (header >> 10) & 0x3FF
        rec_len = (header >> 20) & 0xFFF
        i += 4
        if rec_len == 0xFFF:
            rec_len = struct.unpack_from("<I", data, i)[0]
            i += 4
        payload = data[i : i + rec_len]
        i += rec_len
        # HWPTAG_PARA_TEXT = 67
        if tag_id == 67:
            text_parts.append(decode_para_text(payload))
    return "".join(text_parts)


def decode_para_text(payload):
    chars = []
    i = 0
    n = len(payload)
    while i + 1 < n:
        code = struct.unpack_from("<H", payload, i)[0]
        if code in (0, 10, 13):
            chars.append("\n" if code in (10, 13) else "")
            i += 2
        elif 1 <= code <= 31:
            # control chars, some are 8 bytes (inline/extended)
            if code in (1, 2, 3, 11, 12, 14, 15, 16, 17, 18, 21, 22, 23):
                i += 16
            else:
                i += 2
        else:
            chars.append(chr(code))
            i += 2
    return "".join(chars)


if __name__ == "__main__":
    path = sys.argv[1]
    text = extract_text(path)
    out_path = sys.argv[2] if len(sys.argv) > 2 else "scripts/hwp_dump.txt"
    with open(out_path, "wb") as fh:
        fh.write(text.encode("utf-8", "surrogatepass"))
    print(f"Wrote {len(text)} chars to {out_path}")

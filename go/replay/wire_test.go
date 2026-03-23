package replay

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"math"
	"math/big"
	"os"
	"path/filepath"
	"testing"
)

// --- helpers to build wire-format bytes ---

func controlByte(sizedType, sizeNibble byte) byte {
	return (sizeNibble << 4) | sizedType
}

func fixedControl(fixedType byte) byte {
	return (fixedType << 4) | stFixedSize
}

func appendSized(buf []byte, st byte, size int, payload []byte) []byte {
	if size <= 11 {
		buf = append(buf, controlByte(st, byte(size)))
	} else if size < 255 {
		buf = append(buf, controlByte(st, oneByteSize), byte(size))
	} else if size < 65535 {
		buf = append(buf, controlByte(st, twoByteSize))
		buf = binary.LittleEndian.AppendUint16(buf, uint16(size))
	} else {
		buf = append(buf, controlByte(st, fourByteSize))
		buf = binary.LittleEndian.AppendUint32(buf, uint32(size))
	}
	if payload != nil {
		buf = append(buf, payload...)
	}
	return buf
}

func appendStr(buf []byte, s string) []byte {
	return appendSized(buf, stStr, len(s), []byte(s))
}

func appendUint(buf []byte, v uint64) []byte {
	if v <= 11 {
		return append(buf, controlByte(stUint, byte(v)))
	} else if v < 255 {
		return append(buf, controlByte(stUint, oneByteSize), byte(v))
	} else if v < 65535 {
		b := controlByte(stUint, twoByteSize)
		buf = append(buf, b)
		return binary.LittleEndian.AppendUint16(buf, uint16(v))
	} else if v < math.MaxUint32 {
		b := controlByte(stUint, fourByteSize)
		buf = append(buf, b)
		return binary.LittleEndian.AppendUint32(buf, uint32(v))
	}
	b := controlByte(stUint, eightByteSize)
	buf = append(buf, b)
	return binary.LittleEndian.AppendUint64(buf, v)
}

// --- Tests ---

func TestFixedNone(t *testing.T) {
	dec := NewDecoder(bytes.NewReader([]byte{fixedControl(ftNone)}))
	v, err := dec.ReadValue()
	if err != nil {
		t.Fatal(err)
	}
	if v != nil {
		t.Fatalf("expected nil, got %v", v)
	}
}

func TestFixedThreadLifecycleUnhandled(t *testing.T) {
	data := []byte{fixedControl(ftThreadEnter), fixedControl(ftThreadExit)}
	dec := NewDecoder(bytes.NewReader(data))

	if _, err := dec.ReadValue(); err == nil {
		t.Fatal("expected thread-enter fixed control to be unhandled")
	}
	if _, err := dec.ReadValue(); err == nil {
		t.Fatal("expected thread-exit fixed control to be unhandled")
	}
}

func TestFixedInt64(t *testing.T) {
	var buf []byte
	buf = append(buf, fixedControl(ftInt64))
	buf = binary.LittleEndian.AppendUint64(buf, uint64(-42&0xFFFFFFFFFFFFFFFF))

	dec := NewDecoder(bytes.NewReader(buf))
	v, err := dec.ReadValue()
	if err != nil {
		t.Fatal(err)
	}
	if v != int64(-42) {
		t.Fatalf("expected int64(-42), got %v (%T)", v, v)
	}
}

func TestFixedInt64Neg1(t *testing.T) {
	var buf []byte
	buf = append(buf, fixedControl(ftInt64))
	buf = binary.LittleEndian.AppendUint64(buf, ^uint64(0))

	dec := NewDecoder(bytes.NewReader(buf))
	v, err := dec.ReadValue()
	if err != nil {
		t.Fatal(err)
	}
	if v != int64(-1) {
		t.Fatalf("expected int64(-1), got %v (%T)", v, v)
	}
}

func TestFixedFloat(t *testing.T) {
	var buf []byte
	buf = append(buf, fixedControl(ftFloat))
	buf = binary.LittleEndian.AppendUint64(buf, math.Float64bits(3.14))

	dec := NewDecoder(bytes.NewReader(buf))
	v, err := dec.ReadValue()
	if err != nil {
		t.Fatal(err)
	}
	f, ok := v.(float64)
	if !ok || f != 3.14 {
		t.Fatalf("expected float64(3.14), got %v (%T)", v, v)
	}
}

func TestUintInline(t *testing.T) {
	for _, n := range []uint64{0, 5, 11} {
		buf := appendUint(nil, n)
		dec := NewDecoder(bytes.NewReader(buf))
		v, err := dec.ReadValue()
		if err != nil {
			t.Fatalf("n=%d: %v", n, err)
		}
		if v != int64(n) {
			t.Fatalf("n=%d: expected int64(%d), got %v (%T)", n, n, v, v)
		}
	}
}

func TestUintOneByte(t *testing.T) {
	buf := appendUint(nil, 200)
	dec := NewDecoder(bytes.NewReader(buf))
	v, err := dec.ReadValue()
	if err != nil {
		t.Fatal(err)
	}
	if v != int64(200) {
		t.Fatalf("expected int64(200), got %v (%T)", v, v)
	}
}

func TestUintTwoByte(t *testing.T) {
	buf := appendUint(nil, 1000)
	dec := NewDecoder(bytes.NewReader(buf))
	v, err := dec.ReadValue()
	if err != nil {
		t.Fatal(err)
	}
	if v != int64(1000) {
		t.Fatalf("expected int64(1000), got %v (%T)", v, v)
	}
}

func TestUintFourByte(t *testing.T) {
	buf := appendUint(nil, 100000)
	dec := NewDecoder(bytes.NewReader(buf))
	v, err := dec.ReadValue()
	if err != nil {
		t.Fatal(err)
	}
	if v != int64(100000) {
		t.Fatalf("expected int64(100000), got %v (%T)", v, v)
	}
}

func TestString(t *testing.T) {
	buf := appendStr(nil, "hello")
	dec := NewDecoder(bytes.NewReader(buf))
	v, err := dec.ReadValue()
	if err != nil {
		t.Fatal(err)
	}
	if v != "hello" {
		t.Fatalf("expected %q, got %v", "hello", v)
	}
	if len(dec.internedStrings) != 1 || dec.internedStrings[0] != "hello" {
		t.Fatalf("string not interned")
	}
}

func TestStrRef(t *testing.T) {
	// Write "hello" (interned as index 0), then STR_REF(0)
	var buf []byte
	buf = appendStr(buf, "hello")
	buf = appendSized(buf, stStrRef, 0, nil)

	dec := NewDecoder(bytes.NewReader(buf))
	v1, err := dec.ReadValue()
	if err != nil {
		t.Fatal(err)
	}
	v2, err := dec.ReadValue()
	if err != nil {
		t.Fatal(err)
	}
	if v1 != "hello" || v2 != "hello" {
		t.Fatalf("expected both hello, got %v, %v", v1, v2)
	}
}

func TestLongString(t *testing.T) {
	s := string(make([]byte, 300))
	for i := range []byte(s) {
		s = s[:i] + "a" + s[i+1:]
	}
	s = ""
	for i := 0; i < 300; i++ {
		s += "x"
	}
	buf := appendStr(nil, s)
	dec := NewDecoder(bytes.NewReader(buf))
	v, err := dec.ReadValue()
	if err != nil {
		t.Fatal(err)
	}
	if v != s {
		t.Fatalf("string mismatch: len got %d, want %d", len(v.(string)), len(s))
	}
}

func TestBytes(t *testing.T) {
	data := []byte{0xDE, 0xAD, 0xBE, 0xEF}
	buf := appendSized(nil, stBytes, len(data), data)

	dec := NewDecoder(bytes.NewReader(buf))
	v, err := dec.ReadValue()
	if err != nil {
		t.Fatal(err)
	}
	b, ok := v.([]byte)
	if !ok {
		t.Fatalf("expected []byte, got %T", v)
	}
	if !bytes.Equal(b, data) {
		t.Fatalf("bytes mismatch")
	}
}

func TestList(t *testing.T) {
	// LIST of 2 elements: "a", uint(42)
	var buf []byte
	buf = append(buf, controlByte(stList, 2))
	buf = appendStr(buf, "a")
	buf = appendUint(buf, 42)

	dec := NewDecoder(bytes.NewReader(buf))
	v, err := dec.ReadValue()
	if err != nil {
		t.Fatal(err)
	}
	lst, ok := v.([]any)
	if !ok || len(lst) != 2 {
		t.Fatalf("expected []any of len 2, got %T %v", v, v)
	}
	if lst[0] != "a" {
		t.Fatalf("lst[0]: expected %q, got %v", "a", lst[0])
	}
	if lst[1] != int64(42) {
		t.Fatalf("lst[1]: expected int64(42), got %v (%T)", lst[1], lst[1])
	}
}

func TestTuple(t *testing.T) {
	var buf []byte
	buf = append(buf, controlByte(stTuple, 1))
	buf = appendUint(buf, 1)

	dec := NewDecoder(bytes.NewReader(buf))
	v, err := dec.ReadValue()
	if err != nil {
		t.Fatal(err)
	}
	lst, ok := v.([]any)
	if !ok || len(lst) != 1 || lst[0] != int64(1) {
		t.Fatalf("expected [1], got %v", v)
	}
}

func TestDict(t *testing.T) {
	// DICT with 2 pairs: "key1" -> 1, "key2" -> "val"
	var buf []byte
	buf = append(buf, controlByte(stDict, 2))
	buf = appendStr(buf, "key1")
	buf = appendUint(buf, 1)
	buf = appendStr(buf, "key2")
	buf = appendStr(buf, "val")

	dec := NewDecoder(bytes.NewReader(buf))
	v, err := dec.ReadValue()
	if err != nil {
		t.Fatal(err)
	}
	m, ok := v.(map[string]any)
	if !ok {
		t.Fatalf("expected map, got %T", v)
	}
	if m["key1"] != int64(1) {
		t.Fatalf("key1: expected int64(1), got %v", m["key1"])
	}
	if m["key2"] != "val" {
		t.Fatalf("key2: expected %q, got %v", "val", m["key2"])
	}
}

func TestNestedDict(t *testing.T) {
	// {"outer": {"inner": 1}}
	var buf []byte
	buf = append(buf, controlByte(stDict, 1))
	buf = appendStr(buf, "outer")
	buf = append(buf, controlByte(stDict, 1))
	buf = appendStr(buf, "inner")
	buf = appendUint(buf, 1)

	dec := NewDecoder(bytes.NewReader(buf))
	v, err := dec.ReadValue()
	if err != nil {
		t.Fatal(err)
	}
	m := v.(map[string]any)
	inner := m["outer"].(map[string]any)
	if inner["inner"] != int64(1) {
		t.Fatalf("expected int64(1), got %v", inner["inner"])
	}
}

func TestBigint(t *testing.T) {
	// Positive: 256 = 0x0100 in big-endian
	var buf []byte
	raw := []byte{0x01, 0x00}
	buf = appendSized(buf, stBigint, len(raw), raw)

	dec := NewDecoder(bytes.NewReader(buf))
	v, err := dec.ReadValue()
	if err != nil {
		t.Fatal(err)
	}
	bi := v.(*big.Int)
	if bi.Int64() != 256 {
		t.Fatalf("expected 256, got %v", bi)
	}
}

func TestBigintNegative(t *testing.T) {
	// -1 in two's complement big-endian: 0xFF
	var buf []byte
	buf = appendSized(buf, stBigint, 1, []byte{0xFF})

	dec := NewDecoder(bytes.NewReader(buf))
	v, err := dec.ReadValue()
	if err != nil {
		t.Fatal(err)
	}
	bi := v.(*big.Int)
	if bi.Int64() != -1 {
		t.Fatalf("expected -1, got %v", bi)
	}
}

func TestReadRootValueSkipsControlMessages(t *testing.T) {
	// Simulate: NEW_HANDLE(str "handle_val"), ADD_FILENAME(str "file.py"),
	// then the actual data dict {"k": "v"}.
	var buf []byte

	// NEW_HANDLE followed by a string value
	buf = append(buf, fixedControl(ftNewHandle))
	buf = appendStr(buf, "handle_val")

	// ADD_FILENAME followed by a string value
	buf = append(buf, fixedControl(ftAddFilename))
	buf = appendStr(buf, "file.py")

	// THREAD_SWITCH (no payload)
	buf = append(buf, fixedControl(ftThreadSwitch))

	// The preamble dict
	buf = append(buf, controlByte(stDict, 1))
	buf = appendStr(buf, "k")
	buf = appendStr(buf, "v")

	dec := NewDecoder(bytes.NewReader(buf))
	m, err := dec.ReadRootValue()
	if err != nil {
		t.Fatal(err)
	}
	if m["k"] != "v" {
		t.Fatalf("expected {k: v}, got %v", m)
	}
}

func TestReadRootValueSkipsDelete(t *testing.T) {
	// DELETE with size=3, then a dict
	var buf []byte
	buf = append(buf, controlByte(stDelete, 3))
	buf = append(buf, controlByte(stDict, 1))
	buf = appendStr(buf, "a")
	buf = appendUint(buf, 1)

	dec := NewDecoder(bytes.NewReader(buf))
	m, err := dec.ReadRootValue()
	if err != nil {
		t.Fatal(err)
	}
	if m["a"] != int64(1) {
		t.Fatalf("expected {a: 1}, got %v", m)
	}
}

func TestReadProcessSkipsShebang(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "test.bin")

	content := "#!/usr/bin/env replay\n" + `{"executable":"/usr/bin/python3","cwd":"/app","type":"exec"}` + "\n"
	if err := os.WriteFile(p, []byte(content), 0644); err != nil {
		t.Fatal(err)
	}

	m, err := ReadProcess(p)
	if err != nil {
		t.Fatalf("ReadProcess: %v", err)
	}
	if m["executable"] != "/usr/bin/python3" {
		t.Fatalf("executable = %v, want /usr/bin/python3", m["executable"])
	}
	if m["cwd"] != "/app" {
		t.Fatalf("cwd = %v, want /app", m["cwd"])
	}
	if m["recording"] != p {
		t.Fatalf("recording = %v, want %v", m["recording"], p)
	}
}

func TestReadProcessWithoutShebang(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "test.bin")

	content := `{"executable":"/usr/bin/python3","cwd":"/app"}` + "\n"
	if err := os.WriteFile(p, []byte(content), 0644); err != nil {
		t.Fatal(err)
	}

	m, err := ReadProcess(p)
	if err != nil {
		t.Fatalf("ReadProcess: %v", err)
	}
	if m["executable"] != "/usr/bin/python3" {
		t.Fatalf("executable = %v, want /usr/bin/python3", m["executable"])
	}
}

// --- helpers to build JSON process info payloads ---

func makeJSONPayload(info map[string]any) []byte {
	j, _ := json.Marshal(info)
	var buf []byte
	lenBuf := make([]byte, 4)
	binary.LittleEndian.PutUint32(lenBuf, uint32(len(j)))
	buf = append(buf, lenBuf...)
	buf = append(buf, j...)
	buf = append(buf, '\n')
	return buf
}

func makePIDFrame(pid uint32, payload []byte) []byte {
	hdr := make([]byte, 6)
	binary.LittleEndian.PutUint32(hdr[0:4], pid)
	binary.LittleEndian.PutUint16(hdr[4:6], uint16(len(payload)))
	return append(hdr, payload...)
}

// --- Demux + ReadProcess integration test ---

func TestDemuxAndReadProcess(t *testing.T) {
	payload := makeJSONPayload(map[string]any{
		"argv":             "test",
		"pid":              42,
		"encoding_version": 1,
	})

	pid := uint32(12345)
	trace := makePIDFrame(pid, payload)

	tmpDir := t.TempDir()
	tracePath := filepath.Join(tmpDir, "trace.bin")
	if err := os.WriteFile(tracePath, trace, 0644); err != nil {
		t.Fatal(err)
	}

	outDir := filepath.Join(tmpDir, "demuxed")
	if err := os.MkdirAll(outDir, 0755); err != nil {
		t.Fatal(err)
	}

	ch, waitDemux := Demux(tracePath, outDir)
	processes, waitMap := MapProcesses(ch)

	var got []map[string]any
	for m := range processes {
		got = append(got, m)
	}
	if err := waitDemux(); err != nil {
		t.Fatal(err)
	}
	if err := waitMap(); err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 {
		t.Fatalf("expected 1 process, got %d", len(got))
	}

	m := got[0]
	if m["argv"] != "test" {
		t.Fatalf("argv: expected %q, got %v", "test", m["argv"])
	}
	if m["pid"] != float64(42) {
		t.Fatalf("pid: expected 42, got %v", m["pid"])
	}
	rec, ok := m["recording"].(string)
	if !ok || rec == "" {
		t.Fatalf("recording: expected non-empty path, got %v", m["recording"])
	}
}

func TestDemuxMultipleProcesses(t *testing.T) {
	p1 := makeJSONPayload(map[string]any{"name": "alice"})
	p2 := makeJSONPayload(map[string]any{"name": "bob"})

	var trace []byte
	trace = append(trace, makePIDFrame(100, p1)...)
	trace = append(trace, makePIDFrame(200, p2)...)
	trace = append(trace, makePIDFrame(100, nil)...)

	tmpDir := t.TempDir()
	tracePath := filepath.Join(tmpDir, "trace.bin")
	os.WriteFile(tracePath, trace, 0644)

	outDir := filepath.Join(tmpDir, "out")
	os.MkdirAll(outDir, 0755)

	ch, waitDemux := Demux(tracePath, outDir)
	processes, waitMap := MapProcesses(ch)

	names := make(map[string]bool)
	for m := range processes {
		names[m["name"].(string)] = true
	}
	if err := waitDemux(); err != nil {
		t.Fatal(err)
	}
	if err := waitMap(); err != nil {
		t.Fatal(err)
	}
	if !names["alice"] || !names["bob"] {
		t.Fatalf("expected alice and bob, got %v", names)
	}
}

func TestMapProcessesFirstIsMainPID(t *testing.T) {
	var trace []byte
	for _, pid := range []uint32{10, 20, 30} {
		payload := makeJSONPayload(map[string]any{"name": fmt.Sprintf("pid_%d", pid)})
		trace = append(trace, makePIDFrame(pid, payload)...)
		for i := 0; i < 49; i++ {
			trace = append(trace, makePIDFrame(pid, nil)...)
		}
	}

	tmpDir := t.TempDir()
	tracePath := filepath.Join(tmpDir, "trace.bin")
	os.WriteFile(tracePath, trace, 0644)

	outDir := filepath.Join(tmpDir, "out")
	os.MkdirAll(outDir, 0755)

	ch, waitDemux := Demux(tracePath, outDir)
	processes, waitMap := MapProcesses(ch)

	first := true
	for m := range processes {
		if _, ok := m["recording"].(string); !ok {
			t.Fatal("missing recording key")
		}
		if first {
			if m["name"] != "pid_10" {
				t.Fatalf("first process should be main PID; expected pid_10, got %v", m["name"])
			}
			first = false
		}
	}
	if err := waitDemux(); err != nil {
		t.Fatal(err)
	}
	if err := waitMap(); err != nil {
		t.Fatal(err)
	}
}

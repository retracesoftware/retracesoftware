package replay

import (
	"bufio"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"math/big"
	"os"
	"path/filepath"
)

// SizedTypes — lower 4 bits of the control byte when not FIXED_SIZE.
const (
	stBytes       = 0
	stList        = 1
	stDict        = 2
	stTuple       = 3
	stStr         = 4
	stPickled     = 5
	stUint        = 6
	stDelete      = 7
	stHandle      = 8
	stBigint      = 9
	stSet         = 10
	stFrozenset   = 11
	stBinding     = 12
	stBindingDel  = 13
	stStrRef      = 14
	stFixedSize   = 15
)

// FixedSizeTypes — upper 4 bits when lower 4 == stFixedSize.
const (
	ftNone         = 0
	ftThreadEnter  = 1
	ftThreadExit   = 2
	ftFloat        = 3
	ftInt64        = 4
	ftBind         = 5
	ftInternInline = 6
	ftThreadSwitch = 7
	ftNewHandle    = 8
	ftStack        = 9
	ftAddFilename  = 10
	ftChecksum     = 11
	ftDropped      = 12
	ftHeartbeat    = 13
	ftSerializeErr = 14
)

// Size class markers in the upper 4 bits (for sized types).
const (
	oneByteSize   = 12
	twoByteSize   = 13
	fourByteSize  = 14
	eightByteSize = 15
)

// Decoder reads wire-format values from a raw (unframed) byte stream.
type Decoder struct {
	r               io.Reader
	internedStrings []string
	buf             [8]byte // scratch for multi-byte reads
}

// NewDecoder creates a Decoder that reads from r.
func NewDecoder(r io.Reader) *Decoder {
	return &Decoder{r: r}
}

func (d *Decoder) readByte() (byte, error) {
	_, err := io.ReadFull(d.r, d.buf[:1])
	return d.buf[0], err
}

func (d *Decoder) readBytes(n int) ([]byte, error) {
	buf := make([]byte, n)
	_, err := io.ReadFull(d.r, buf)
	return buf, err
}

func (d *Decoder) readU16() (uint16, error) {
	if _, err := io.ReadFull(d.r, d.buf[:2]); err != nil {
		return 0, err
	}
	return binary.LittleEndian.Uint16(d.buf[:2]), nil
}

func (d *Decoder) readU32() (uint32, error) {
	if _, err := io.ReadFull(d.r, d.buf[:4]); err != nil {
		return 0, err
	}
	return binary.LittleEndian.Uint32(d.buf[:4]), nil
}

func (d *Decoder) readU64() (uint64, error) {
	if _, err := io.ReadFull(d.r, d.buf[:8]); err != nil {
		return 0, err
	}
	return binary.LittleEndian.Uint64(d.buf[:8]), nil
}

// readSize decodes the size from the upper 4 bits of a control byte.
// For inline sizes (0-11) the value is returned directly. For extended
// sizes (12-15) the appropriate number of LE bytes are read.
func (d *Decoder) readSize(upper byte) (uint64, error) {
	switch upper {
	case oneByteSize:
		b, err := d.readByte()
		return uint64(b), err
	case twoByteSize:
		v, err := d.readU16()
		return uint64(v), err
	case fourByteSize:
		v, err := d.readU32()
		return uint64(v), err
	case eightByteSize:
		v, err := d.readU64()
		return v, err
	default:
		return uint64(upper), nil
	}
}

// ReadValue reads one wire-format value and returns it as a Go type:
//   - nil for None
//   - int64 for INT64 and small UINT
//   - uint64 for large UINT
//   - *big.Int for BIGINT
//   - float64 for FLOAT
//   - string for STR / STR_REF
//   - []byte for BYTES / PICKLED
//   - []any for LIST / TUPLE / SET / FROZENSET
//   - map[string]any for DICT
func (d *Decoder) ReadValue() (any, error) {
	cb, err := d.readByte()
	if err != nil {
		return nil, err
	}
	return d.readControl(cb)
}

func (d *Decoder) readControl(cb byte) (any, error) {
	lower := cb & 0x0F
	upper := (cb >> 4) & 0x0F

	if lower == stFixedSize {
		return d.readFixed(upper)
	}
	return d.readSized(lower, upper)
}

func (d *Decoder) readFixed(ft byte) (any, error) {
	switch ft {
	case ftNone:
		return nil, nil
	case ftFloat:
		v, err := d.readU64()
		if err != nil {
			return nil, err
		}
		return math.Float64frombits(v), nil
	case ftInt64:
		v, err := d.readU64()
		if err != nil {
			return nil, err
		}
		return int64(v), nil
	default:
		return nil, fmt.Errorf("unhandled fixed type %d (0x%02X)", ft, (ft<<4)|stFixedSize)
	}
}

func (d *Decoder) readSized(st byte, sizeNibble byte) (any, error) {
	size, err := d.readSize(sizeNibble)
	if err != nil {
		return nil, err
	}

	switch st {
	case stUint:
		if size <= math.MaxInt64 {
			return int64(size), nil
		}
		return size, nil

	case stStr:
		buf, err := d.readBytes(int(size))
		if err != nil {
			return nil, err
		}
		s := string(buf)
		d.internedStrings = append(d.internedStrings, s)
		return s, nil

	case stStrRef:
		idx := int(size)
		if idx < 0 || idx >= len(d.internedStrings) {
			return nil, fmt.Errorf("STR_REF index %d out of range (have %d)", idx, len(d.internedStrings))
		}
		return d.internedStrings[idx], nil

	case stDict:
		m := make(map[string]any, size)
		for i := uint64(0); i < size; i++ {
			kv, err := d.ReadValue()
			if err != nil {
				return nil, fmt.Errorf("dict key %d: %w", i, err)
			}
			key, ok := kv.(string)
			if !ok {
				return nil, fmt.Errorf("dict key %d: expected string, got %T", i, kv)
			}
			val, err := d.ReadValue()
			if err != nil {
				return nil, fmt.Errorf("dict value for %q: %w", key, err)
			}
			m[key] = val
		}
		return m, nil

	case stList, stTuple, stSet, stFrozenset:
		elems := make([]any, size)
		for i := uint64(0); i < size; i++ {
			v, err := d.ReadValue()
			if err != nil {
				return nil, fmt.Errorf("list/tuple element %d: %w", i, err)
			}
			elems[i] = v
		}
		return elems, nil

	case stBytes:
		return d.readBytes(int(size))

	case stPickled:
		return d.readBytes(int(size))

	case stBigint:
		raw, err := d.readBytes(int(size))
		if err != nil {
			return nil, err
		}
		// big-endian signed two's complement
		bi := new(big.Int).SetBytes(raw)
		if len(raw) > 0 && raw[0]&0x80 != 0 {
			// negative: subtract 2^(8*len)
			modulus := new(big.Int).Lsh(big.NewInt(1), uint(8*len(raw)))
			bi.Sub(bi, modulus)
		}
		return bi, nil

	case stHandle:
		return nil, fmt.Errorf("HANDLE reference (index %d) not supported in preamble context", size)

	case stBinding:
		return nil, fmt.Errorf("BINDING reference (id %d) not supported in preamble context", size)

	case stDelete, stBindingDel:
		// stream-level: skip
		return nil, fmt.Errorf("unexpected stream control (type %d) in value context", st)

	default:
		return nil, fmt.Errorf("unknown sized type %d", st)
	}
}

// skipValue reads and discards one wire-format value.
func (d *Decoder) skipValue() error {
	_, err := d.ReadValue()
	return err
}

// readExpectedInt reads the compact int encoding used by STACK frames:
// one byte; if 255, followed by a uint64 LE.
func (d *Decoder) readExpectedInt() (uint64, error) {
	b, err := d.readByte()
	if err != nil {
		return 0, err
	}
	if b == 255 {
		return d.readU64()
	}
	return uint64(b), nil
}

// skipStack consumes a STACK frame: expected-int count, then count
// entries of (uint16 filename_index, uint16 lineno).
func (d *Decoder) skipStack() error {
	n, err := d.readExpectedInt()
	if err != nil {
		return err
	}
	// Each entry is 2 + 2 = 4 bytes.
	skip := int(n) * 4
	_, err = d.readBytes(skip)
	return err
}

// ReadProcess opens a PidFile and reads the JSON preamble line.
// If the file starts with a shebang (#!) line, it is skipped.
// The file path is added under key "recording".
func ReadProcess(path string) (map[string]any, error) {
	absPath, err := filepath.Abs(path)
	if err != nil {
		return nil, fmt.Errorf("resolve absolute path: %w", err)
	}

	f, err := os.Open(absPath)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	reader := bufio.NewReader(f)
	line, err := reader.ReadBytes('\n')
	if err != nil {
		return nil, fmt.Errorf("read process info line: %w", err)
	}

	if len(line) >= 2 && line[0] == '#' && line[1] == '!' {
		line, err = reader.ReadBytes('\n')
		if err != nil {
			return nil, fmt.Errorf("read process info line after shebang: %w", err)
		}
	}

	var m map[string]any
	if err := json.Unmarshal(line, &m); err != nil {
		return nil, fmt.Errorf("parse process info JSON: %w", err)
	}
	m["recording"] = absPath
	return m, nil
}

// MapProcesses consumes per-PID file paths from in, reads the preamble
// from each, and sends the resulting process dicts to the returned
// channel. The first process emitted corresponds to the main PID
// (i.e. the first PID encountered in the trace). The channel is closed
// when in is exhausted. Call wait to retrieve any error.
func MapProcesses(in <-chan string) (<-chan map[string]any, func() error) {
	out := make(chan map[string]any)
	errCh := make(chan error, 1)

	go func() {
		defer close(out)
		for path := range in {
			m, err := ReadProcess(path)
			if err != nil {
				errCh <- err
				return
			}
			out <- m
		}
		errCh <- nil
	}()

	return out, func() error { return <-errCh }
}

// ReadRootValue reads the next root-level data value, consuming any
// stream-level control messages that precede it (NEW_HANDLE, ADD_FILENAME,
// DELETE, BINDING_DELETE, INTERN, THREAD_SWITCH, BIND, STACK, etc.).
func (d *Decoder) ReadRootValue() (map[string]any, error) {
	for {
		cb, err := d.readByte()
		if err != nil {
			return nil, err
		}

		lower := cb & 0x0F
		upper := (cb >> 4) & 0x0F

		if lower == stFixedSize {
			switch upper {
			case ftNewHandle:
				if err := d.skipValue(); err != nil {
					return nil, fmt.Errorf("skip NEW_HANDLE value: %w", err)
				}
				continue
			case ftAddFilename:
				if err := d.skipValue(); err != nil {
					return nil, fmt.Errorf("skip ADD_FILENAME value: %w", err)
				}
				continue
			case ftInternInline:
				if err := d.skipValue(); err != nil {
					return nil, fmt.Errorf("skip INTERN value: %w", err)
				}
				continue
			case ftBind, ftThreadSwitch, ftChecksum, ftDropped, ftHeartbeat:
				continue
			case ftStack:
				if err := d.skipStack(); err != nil {
					return nil, fmt.Errorf("skip STACK: %w", err)
				}
				continue
			}
		} else {
			switch lower {
			case stDelete, stBindingDel:
				// consume the size field and continue
				if _, err := d.readSize(upper); err != nil {
					return nil, fmt.Errorf("skip DELETE/BINDING_DELETE size: %w", err)
				}
				continue
			}
		}

		// Not a control message — this is the data value.
		val, err := d.readControl(cb)
		if err != nil {
			return nil, err
		}
		m, ok := val.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("expected dict preamble, got %T", val)
		}
		return m, nil
	}
}

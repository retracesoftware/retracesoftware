#pragma once
#include <cstdint>

namespace retracesoftware_stream {
    // first bit encodes if its a sized type
    // have a intern call on writer, Can cache commonly used strings
    // 
    // 1111XXXX - means fixed-size...
    // we have lookup, which is unsigned, stops at 4 byte
    // we have DEL_1BYTE_INDEX
    // we have DEL_2BYTE_INDEX
    // we have DEL_2BYTE_INDEX
    // deletes run backwards from last index, most deletes are most recent which is smaller more fixed index
    // will compress better also,
    // also have lookup_from_front and lookup_from_back,

    enum SizedTypes : uint8_t {
        BYTES,
        LIST,
        DICT,
        TUPLE,

        STR,
        PICKLED,
        UINT,
        BIGINT,

        SET,
        FROZENSET,
        BINDING,
        BINDING_DELETE,
        INTERN,
        
        STR_REF,  // Reference to previously-written interned string
        FIXED_SIZE,
        SizedTypes__LAST__,
    };


    // 01111000 0x78

    // #define SIZE_MASK 0x78 

    // FIXEDSIZE = 0xF0

    // #define SIZE_MASK 0xF0

    enum Sizes : uint8_t {
        ONE_BYTE_SIZE = 12,
        TWO_BYTE_SIZE,
        FOUR_BYTE_SIZE,
        EIGHT_BYTE_SIZE,
    };

    // #define ONE_BYTE_SIZE   (0x0B << 4)
    // #define TWO_BYTE_SIZE   (0x0C << 4)
    // #define FOUR_BYTE_SIZE  (0x0D << 4)
    // #define EIGHT_BYTE_SIZE (0x0E << 4)
    // #define FIXED_SIZE      (0x0F << 4)

    // #define IS_ROOT_TYPE 0x06

    enum FixedSizeTypes : uint8_t {
        NONE,
        THREAD_ENTER,
        THREAD_EXIT,
        FLOAT,
        INT64,
        BIND,
        INTERN_INLINE,

        THREAD_SWITCH,
        NEW_PATCHED,
        STACK,
        ADD_FILENAME,
        
        CHECKSUM,
        DROPPED,
        HEARTBEAT,
        SERIALIZE_ERROR,

        FixedSizeTypes__LAST__,
    };

    // enum RootOnlyTypes : uint8_t {
    //     THREAD_SWITCH = FixedSizeTypes::FixedSizeTypes__LAST__,
    //     BIND,
    //     Stack,
    //     ADD_FILENAME,
    //     CHECKSUM,
    //     RootOnlyTypes__LAST__,
    // };

    union Control {
        struct {
            SizedTypes type : 4;  // Lower 4 bits
            Sizes size : 4; // Upper 4 bits
        } Sized;
        struct {
            SizedTypes SizedTypes_FIXED_SIZE : 4;  // Lower 4 bits
            FixedSizeTypes type : 4; // Upper 4 bits
        } Fixed;
        uint8_t raw;

        constexpr Control(SizedTypes st, FixedSizeTypes ft) 
            : Fixed{st, ft} {}

        Control(uint8_t raw) : raw(raw) {}
        Control() : raw(0) {}

        bool operator==(const Control& other) const {
            return this->raw == other.raw;
        }
        
        bool operator!=(const Control& other) const {
            return !(*this == other);
        }
    };

    // static bool is_fixedsize(Control control) {
    //     return control.Fixed.SizedTypes_FIXED_SIZE == FIXED_SIZE;
    // }

    // static SizedTypes sized_type(Control control) {
    //     return control.Sized.type != FIXED_SIZE ? control.Sized.type : SizedTypes__LAST__;
    // }

    static constexpr Control create_fixed_size(FixedSizeTypes type) {
        return Control(SizedTypes::FIXED_SIZE, type);
    }
    
    constexpr Control Stack = create_fixed_size(FixedSizeTypes::STACK);
    constexpr Control ThreadSwitch = create_fixed_size(FixedSizeTypes::THREAD_SWITCH);
    constexpr Control AddFilename = create_fixed_size(FixedSizeTypes::ADD_FILENAME);
    constexpr Control Empty = ThreadSwitch;

    constexpr Control Checksum = create_fixed_size(FixedSizeTypes::CHECKSUM);
    constexpr Control Dropped = create_fixed_size(FixedSizeTypes::DROPPED);
    constexpr Control Heartbeat = create_fixed_size(FixedSizeTypes::HEARTBEAT);
    constexpr Control SerializeError = create_fixed_size(FixedSizeTypes::SERIALIZE_ERROR);
    constexpr Control Bind = create_fixed_size(FixedSizeTypes::BIND);
    constexpr Control Intern = create_fixed_size(FixedSizeTypes::INTERN_INLINE);
    constexpr Control NewPatched = create_fixed_size(FixedSizeTypes::NEW_PATCHED);
    // constexpr Control BindingDelete = create_fixed_size(FixedSizeTypes::);

    constexpr bool is_binding_delete(Control control) {
        return control.Sized.type == SizedTypes::BINDING_DELETE;
    }

    // static FixedSizeTypes fixed_size_type(Control control) {
    //     return control.Fixed.SizedTypes_FIXED_SIZE == FIXED_SIZE ? control.Fixed.type : FixedSizeTypes__LAST__;
    // }

    inline Control CreateFixedSize(FixedSizeTypes type) {
        Control control;
        control.Fixed.SizedTypes_FIXED_SIZE = SizedTypes::FIXED_SIZE;
        control.Fixed.type = type;
        return control;
    }

    // enum RootTypes : uint8_t {
    //     UNUSED,
    //     NONE_RESULT, // 0 
    //     TRUE_RESULT, // 0
    //     FALSE_RESULT, // 0
    //     RESULT, // 1        
    //     ERROR, // 2
    //     NEW_INSTANCE, // 1
    //     CHECKPOINT, // 1
    //     DUMPSTACK, // 1
    //     PROXYTYPE, // 2
    //     ENTER, // 1
    //     EXIT, // 1
    //     END, // 0
    //     CALL, // 3
    //     SYNC, // 0
    //     BYTES_WRITTEN,
    //     THREAD_SWITCH, // 1
    //     GC_START,
    //     GC_END,
    //     RootTypes__LAST__
    // };

    // inline uint8_t RootTypeSize(RootTypes type) {
    //     switch(type) {
    //         case THREAD_SWITCH: return 1;
    //         case NONE_RESULT: return 0;
    //         case TRUE_RESULT: return 0;
    //         case FALSE_RESULT: return 0;
    //         case RESULT: return 1;
    //         case ERROR: return 2;
    //         case NEW_INSTANCE: return 1;
    //         case CHECKPOINT: return 1;
    //         case PROXYTYPE: return 2;
    //         // case DELETE: return 1;
    //         case ENTER: return 1;
    //         case EXIT: return 1;
    //         case END: return 0;
    //         case CALL: return 3;
    //         case SYNC: return 0;
    //         default: return 0; // or throw
    //     }
    // }

    // const char * RootTypes_Name(enum RootTypes root);
    constexpr const char * FixedSizeTypes_Name(enum FixedSizeTypes root) {
        switch (root) {
            case FixedSizeTypes::NONE: return "NONE";
            case FixedSizeTypes::THREAD_ENTER: return "THREAD_ENTER";
            case FixedSizeTypes::THREAD_EXIT: return "THREAD_EXIT";
            case FixedSizeTypes::FLOAT: return "FLOAT";
            case FixedSizeTypes::INT64: return "INT64";
            case FixedSizeTypes::BIND: return "BIND";
            case FixedSizeTypes::INTERN_INLINE: return "INTERN";
            case FixedSizeTypes::THREAD_SWITCH: return "THREAD_SWITCH";
            case FixedSizeTypes::NEW_PATCHED: return "NEW_PATCHED";
            case FixedSizeTypes::STACK: return "STACK";
            case FixedSizeTypes::ADD_FILENAME: return "ADD_FILENAME";
            case FixedSizeTypes::CHECKSUM: return "CHECKSUM";
            case FixedSizeTypes::DROPPED: return "DROPPED";
            case FixedSizeTypes::HEARTBEAT: return "HEARTBEAT";
            case FixedSizeTypes::SERIALIZE_ERROR: return "SERIALIZE_ERROR";
            default: return nullptr;
        }
    }

    // const char * SizedTypes_Name(enum SizedTypes root);

    constexpr const char * SizedTypes_Name(enum SizedTypes root) {
        switch (root) {
            case SizedTypes::BYTES: return "BYTES";
            case SizedTypes::LIST: return "LIST";
            case SizedTypes::DICT: return "DICT";
            case SizedTypes::TUPLE: return "TUPLE";

            case SizedTypes::STR: return "STR";
            case SizedTypes::PICKLED: return "PICKLED";
            case SizedTypes::UINT: return "UINT";
            case SizedTypes::BIGINT: return "BIGINT";
            case SizedTypes::SET: return "SET";
            case SizedTypes::FROZENSET: return "FROZENSET";

            case SizedTypes::BINDING: return "BINDING";
            case SizedTypes::BINDING_DELETE: return "BINDING_DELETE";
            case SizedTypes::INTERN: return "INTERN";
            case SizedTypes::STR_REF: return "STR_REF";
            case SizedTypes::FIXED_SIZE: return "FIXED_SIZE";

            default: return nullptr;
        }
    }

}

def serializer(obj):
    cls = type(obj)

    try:
        if issubclass(cls, Enum):
            return str(obj)

        if issubclass(cls, (tuple, list)):
            return list(map(serializer, obj))
        elif issubclass(cls, dict):
            return {str(serializer(k)):serializer(v) for k,v in obj.items()}
        elif issubclass(cls, int):
            if obj > 1000000 or obj < -1000000:
                return "XXXX"
            else:
                return int(obj)
        elif issubclass(cls, (bool, str, types.NoneType)):
            return obj
        elif issubclass(cls, types.FunctionType):
            return obj.__qualname__
        elif issubclass(cls, types.CellType):
            return getattr(obj, '__qualname__', 'CellType')
        elif issubclass(cls, types.GeneratorType):
            return obj.__qualname__
        else:
            return cleanse(str(obj))
    except:
        return f'Unserializable object of type: {cls}'

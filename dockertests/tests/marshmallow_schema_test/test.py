from marshmallow import Schema, fields, validate


class UserSchema(Schema):
    id = fields.Int(required=True)
    name = fields.Str(required=True, validate=validate.Length(min=1))
    active = fields.Bool(load_default=True)


def main():
    print("=== marshmallow_schema_test ===")
    schema = UserSchema()
    loaded = schema.load({"id": 7, "name": "Ada"})
    dumped = schema.dump(loaded)
    assert loaded == {"id": 7, "name": "Ada", "active": True}
    print(f"user={dumped['id']} name={dumped['name']} active={dumped['active']}")
    print("marshmallow schema ok")


if __name__ == "__main__":
    main()

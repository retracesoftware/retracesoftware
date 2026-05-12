import jsonpickle


class Node:
    def __init__(self, name):
        self.name = name
        self.children = []


def main():
    print("=== jsonpickle_object_graph_test ===")
    root = Node("root")
    root.children.append(Node("left"))
    root.children.append(Node("right"))

    encoded = jsonpickle.encode(root)
    decoded = jsonpickle.decode(encoded)
    child_names = [child.name for child in decoded.children]
    assert decoded.name == "root"
    assert child_names == ["left", "right"]
    print(f"{decoded.name}:{','.join(child_names)}")
    print("jsonpickle object graph ok")


if __name__ == "__main__":
    main()

from jinja2 import DictLoader, Environment


def main():
    print("=== jinja2_template_test ===")
    env = Environment(
        loader=DictLoader(
            {
                "base.html": "Items:{% block body %}{% endblock %}",
                "items.html": (
                    "{% extends 'base.html' %}"
                    "{% block body %}"
                    "{% for item in items %}[{{ item.name|slug }}={{ item.count }}]{% endfor %}"
                    "{% endblock %}"
                ),
            }
        ),
        autoescape=True,
    )
    env.filters["slug"] = lambda value: value.lower().replace(" ", "-")

    output = env.get_template("items.html").render(
        items=[
            {"name": "Ada Lovelace", "count": 3},
            {"name": "Grace Hopper", "count": 5},
        ]
    )

    print(output)
    assert output == "Items:[ada-lovelace=3][grace-hopper=5]"
    print("jinja2 template ok")


if __name__ == "__main__":
    main()

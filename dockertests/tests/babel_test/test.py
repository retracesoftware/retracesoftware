from babel.numbers import format_currency, format_decimal, format_number


def main():
    # Format a large number in Indian locale
    formatted_number_in = format_number(1234567.89, locale="en_IN")

    # Format a decimal number in French locale
    formatted_decimal_fr = format_decimal(1234567.89, locale="fr_FR")

    # Format currency in the Japanese locale
    formatted_currency_jp = format_currency(1234.50, "JPY", locale="ja_JP")

    print("Formatted number in India:", formatted_number_in, flush=True)
    print("Formatted decimal in France:", formatted_decimal_fr, flush=True)
    print("Formatted currency in Japan:", formatted_currency_jp, flush=True)


if __name__ == "__main__":
    print("=== babel_test ===", flush=True)
    main()

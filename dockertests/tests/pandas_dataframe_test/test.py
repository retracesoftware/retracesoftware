import os

import pandas as pd


def main():
    df = pd.DataFrame(
        {
            "A": [1, 2, 3],
            "B": [4, 5, 6],
        }
    )

    df["C"] = df["A"] + df["B"]
    assert df["C"].tolist() == [5, 7, 9]

    out_path = "test_output.csv"
    df.to_csv(out_path, index=False)
    assert os.path.exists(out_path)

    print("worked worked worked", flush=True)


if __name__ == "__main__":
    print("=== pandas_dataframe_test ===", flush=True)
    main()

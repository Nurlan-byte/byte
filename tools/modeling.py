"""Fixed Poisson pipeline used only in offline experiments."""


def build_poisson_pipeline():
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import PoissonRegressor
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    numeric = Pipeline(
        [
            (
                "impute",
                SimpleImputer(strategy="constant", fill_value=0, add_indicator=True),
            ),
            ("scale", StandardScaler()),
        ]
    )
    preprocess = ColumnTransformer(
        [
            (
                "category",
                OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                [0, 1],
            ),
            ("numeric", numeric, [2, 3]),
        ]
    )
    model = Pipeline(
        [
            ("preprocess", preprocess),
            ("model", PoissonRegressor(alpha=1.0, max_iter=500)),
        ]
    )
    return model

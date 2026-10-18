from .engine import LocalEncoder

if __name__ == "__main__":
    model = LocalEncoder()
    result = model.encode(["Invoice Compass model build check"])
    assert result.shape == (1, 384)
    print("MiniLM model cached; real 384-dimensional inference succeeded.")

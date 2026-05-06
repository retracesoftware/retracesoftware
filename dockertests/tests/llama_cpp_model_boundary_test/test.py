from huggingface_hub import hf_hub_download
from llama_cpp import Llama


def main():
    print("=== llama_cpp_model_boundary_test ===", flush=True)
    model_path = hf_hub_download(
        repo_id="Retrace/fake-model",
        filename="fake-q8.gguf",
    )
    model = Llama(model_path=model_path, n_ctx=32768, verbose=False)
    response = model.create_chat_completion(
        [{"role": "user", "content": "book cheapest flight"}],
        max_tokens=32,
    )
    print(f"MODEL {model_path}", flush=True)
    print(response["choices"][0]["message"]["content"], flush=True)
    print("model boundary ok", flush=True)


if __name__ == "__main__":
    main()

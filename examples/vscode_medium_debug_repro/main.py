from repository import OrderRepository
from service import OrderService


def main():
    repository = OrderRepository()
    service = OrderService(repository)

    summaries = service.build_summaries()

    for line in summaries:
        print(line)


if __name__ == "__main__":
    main()

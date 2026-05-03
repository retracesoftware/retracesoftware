from app.repository import OrderRepository
from app.service import OrderService


def main():
    repository = OrderRepository()
    service = OrderService(repository)

    summaries = service.build_summaries()

    for line in summaries:
        print(line)


if __name__ == "__main__":
    main()

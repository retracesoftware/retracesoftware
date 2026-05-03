import queue
import threading


def build_audit_tags(orders):
    work_queue = queue.Queue()
    result_queue = queue.Queue()

    for index, order in enumerate(orders):
        work_queue.put((index, order.order_id, order.customer))

    def worker():
        while not work_queue.empty():
            index, order_id, customer = work_queue.get()
            tag = f"{index}:{order_id}:{customer.lower()}"
            result_queue.put((order_id, tag))
            work_queue.task_done()

    thread = threading.Thread(target=worker, name="audit-worker")
    thread.start()
    thread.join()

    tags = {}
    while not result_queue.empty():
        order_id, tag = result_queue.get()
        tags[order_id] = tag

    return tags

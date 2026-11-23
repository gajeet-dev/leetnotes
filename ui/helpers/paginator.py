class CustomPaginatorInfo:
    def __init__(self, num_pages: int, total_count: int):
        self.num_pages = max(1, num_pages)
        self.count = total_count


class CustomPage:
    def __init__(
        self, object_list: list, number: int, num_pages: int, total_count: int
    ):
        self.object_list = object_list
        self.number = number
        self.paginator = CustomPaginatorInfo(num_pages, total_count)

    def __len__(self):
        return len(self.object_list)

    def __iter__(self):
        return iter(self.object_list)

    def has_previous(self) -> bool:
        return self.number > 1

    def has_next(self) -> bool:
        return self.number < self.paginator.num_pages

    def previous_page_number(self) -> int:
        return max(1, self.number - 1)

    def next_page_number(self) -> int:
        return min(self.paginator.num_pages, self.number + 1)

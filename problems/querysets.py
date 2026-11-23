from django.db import models
from django.db.models import Q
from django.utils import timezone


class ProblemQuerySet(models.QuerySet):
    def solved(self):
        return self.filter(solved=True)

    def algorithms(self):
        return self.filter(category=self.model.Category.ALGORITHMS)

    def with_submissions(self):
        return self.select_related("submission")

    def mark_as_solved(self):
        return self.filter(solved=False).update(
            solved=True,
            updated_at=timezone.now(),
        )

    def filter_query(self, query):
        return self.filter(
            Q(title__icontains=query)
            | Q(slug__icontains=query)
            | Q(leetcode_id__icontains=query)
        )

    def search(self, query):
        return self.filter_query(query)

    def filter_difficulty(self, difficulty):
        return self.filter(difficulty__iexact=difficulty)

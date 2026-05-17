"""
Нагрузочный тест с распределением по 10 продуктам
Устраняет hot-row contention на одном product_id - показывает реальную пропускную способность

Запуск:
    locust -f tests/load/locustfile_multiproduct.py \
        --host http://localhost:8000 \
        --users 200 --spawn-rate 50 --run-time 60s --headless
"""
import os
import random
import uuid

from locust import HttpUser, between, task

JWT_TOKEN = os.environ.get("LOAD_TEST_JWT", "REPLACE_ME")
# product_id от 1 до 10 - 10 разных строк, конкуренция за каждую минимальна
PRODUCT_IDS = list(range(1, 11))


class PurchaseUser(HttpUser):
    wait_time = between(0, 0)

    @task
    def purchase(self):
        with self.client.post(
            "/api/v1/purchase",
            json={
                "product_id": random.choice(PRODUCT_IDS),
                "purchased_count": 1,
            },
            headers={
                "Authorization": f"Bearer {JWT_TOKEN}",
                "Idempotency-Key": str(uuid.uuid4()),
                "Content-Type": "application/json",
            },
            catch_response=True,
            name="/api/v1/purchase",
        ) as response:
            if response.status_code in (200, 409):
                response.success()
            else:
                response.failure(f"Unexpected status: {response.status_code}")

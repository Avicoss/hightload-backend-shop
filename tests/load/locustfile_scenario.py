"""
Сценарный тест: 1000 пользователей, каждый делает ровно один заказ

Условия:
- Товар: случайно laptop (id=1) или desktop (id=2)
- Количество: случайно 1–10 штук
- Уникальный Idempotency-Key на каждый запрос
- Каждый виртуальный пользователь завершается после одного заказа

Запуск (после seed_scenario.py):
    eval $(python scripts/seed_scenario.py)
    locust -f tests/load/locustfile_scenario.py \
        --host http://api:8000 \
        --users 1000 --spawn-rate 1000 \
        --run-time 30s --headless

Критерии:
    - 0 failures
    - для каждого товара: initial_stock == final_stock + SUM(purchased_count)
"""
import os
import random
import uuid

from locust import HttpUser, constant, task

JWT_TOKEN = os.environ.get("SCENARIO_JWT", "REPLACE_ME")
PRODUCT_IDS = [1, 2]  # laptop, desktop


class OneShotBuyer(HttpUser):
    # Без паузы между задачами - нужен один запрос и стоп
    wait_time = constant(0)

    @task
    def purchase(self) -> None:
        product_id = random.choice(PRODUCT_IDS)
        count = random.randint(1, 10)

        with self.client.post(
            "/api/v1/purchase",
            json={"product_id": product_id, "purchased_count": count},
            headers={
                "Authorization": f"Bearer {JWT_TOKEN}",
                "Idempotency-Key": str(uuid.uuid4()),
            },
            catch_response=True,
            name="/api/v1/purchase",
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Unexpected status: {response.status_code}")

        # Один заказ - стоп. Locust завершается когда все пользователи остановились
        self.stop()

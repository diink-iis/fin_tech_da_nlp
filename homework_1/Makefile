PYTHON ?= python
IMAGE ?= law-links-service
CONTAINER ?= law-links-container

.PHONY: install test eval survey speed run docker-build docker-run docker-stop smoke

install:
	$(PYTHON) -m pip install -r requirements-dev.txt

test:
	$(PYTHON) -m pytest -q

# метрики по нашему, внешнему и синтетическому эталонам без запуска сервиса
eval:
	$(PYTHON) -m eval.run --local main:detect_links --external --synthetic 500 --holdout

survey:
	$(PYTHON) -m eval.survey

# 2000 текстов против лимита проверки (5 минут), сервис должен быть запущен
speed:
	$(PYTHON) -m scripts.speed

run:
	uvicorn main:app --host 0.0.0.0 --port 8978 --reload

docker-build:
	docker build -t $(IMAGE) .

docker-run:
	docker run -d -p 8978:8978 --name $(CONTAINER) $(IMAGE)

docker-stop:
	docker stop $(CONTAINER) && docker rm $(CONTAINER)

smoke:
	curl -sf -X POST "http://localhost:8978/detect" \
		-H "Content-Type: application/json" \
		-d '{"text": "Согласно статье 23 Налогового кодекса и ст. 145 Гражданского кодекса"}'

IMAGE ?= underdog-woof-tts:1.0
NAME  ?= underdog-woof-tts
PORT  ?= 3005

.PHONY: build run stop logs health test weights clean

build:            ## build the image (downloads weights + ffmpeg, verifies sha256)
	docker build -t $(IMAGE) .

run: build        ## run detached with 1 vCPU / 512 MB (matches measured footprint)
	docker run -d --name $(NAME) -p $(PORT):3005 \
		--memory=512m --cpus=1 --restart unless-stopped $(IMAGE)
	@echo "waiting for health..." && sleep 3 && curl -s localhost:$(PORT)/health

stop:
	-docker rm -f $(NAME)

logs:
	docker logs -f $(NAME)

health:
	curl -s localhost:$(PORT)/health

test:             ## end-to-end checks against a running container
	./scripts/smoke_test.sh http://localhost:$(PORT)

weights:          ## download the 40 MB weights locally (needed for `python app.py`)
	python3 scripts/fetch_model.py

clean: stop
	-docker rmi $(IMAGE)

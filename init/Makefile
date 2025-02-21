.PHONY: all

DOCKER_CONFIG ?= "$(HOME)/.docker"

all:
	docker --config $(DOCKER_CONFIG) build --no-cache --output . .

clean:
	rm -rf ./dist

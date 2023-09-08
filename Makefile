.PHONY: all test

all:
	go build -o bin/kohaku cmd/kohaku/main.go

test:
	go test -race -v
	go test -race ./db/test -v
all:
	go build -o bin/kohaku cmd/kohaku/main.go

run: clean all
	./bin/kohaku -c kohaku.ini

clean:
	rm -f bin/kohaku
	rm -rf ./kohaku.log

test:
	go test -race -v
	go test -race ./db/test -v
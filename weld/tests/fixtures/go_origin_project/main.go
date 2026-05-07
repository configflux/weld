package main

import (
	"fmt"
	"example.com/myapi/internal"
	"github.com/example/external"
)

func main() {
	fmt.Println("hello")
	internal.Serve()
	external.Init()
}

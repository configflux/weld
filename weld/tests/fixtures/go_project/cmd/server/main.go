package main

import (
	"fmt"
	"github.com/example/myapi/internal/handlers"
)

func main() {
	fmt.Println("Starting server...")
	handlers.SetupRoutes()
}

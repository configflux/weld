package handlers

import "fmt"

func SetupRoutes() {
	fmt.Println("Setting up routes")
}

func ListUsers() {
	fmt.Println("Listing users")
}

func GetUser(id int) {
	fmt.Printf("Getting user %d\n", id)
}

func CreateUser() {
	fmt.Println("Creating user")
}

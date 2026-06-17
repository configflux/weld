// Package main is a route-emitting fixture for the gin strategy
// (ADR 0071 / criterion 3). It exercises every gin handler-registration
// callsite grammar the strategy recognises: verb methods, route groups,
// Any, and Handle. Kept intentionally small and static.
package main

import (
	"net/http"

	"github.com/gin-gonic/gin"
)

func main() {
	r := gin.Default()

	// Verb methods on the root engine.
	r.GET("/health", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"ok": true})
	})
	r.POST("/users", createUser)
	r.DELETE("/users/:id", func(c *gin.Context) {
		c.Status(http.StatusNoContent)
	})

	// Route group: verb methods on a sub-router. The strategy emits the
	// literal relative path (group-prefix join is a documented non-goal).
	api := r.Group("/api")
	api.GET("/ping", func(c *gin.Context) {
		c.String(http.StatusOK, "pong")
	})
	api.PUT("/config", func(c *gin.Context) {})

	// Any registers a handler for every HTTP verb.
	r.Any("/proxy", func(c *gin.Context) {})

	// Handle takes the method as a literal first argument.
	r.Handle("PATCH", "/users/:id", func(c *gin.Context) {})

	_ = r.Run(":8080")
}

func createUser(c *gin.Context) {
	c.JSON(http.StatusCreated, gin.H{"created": true})
}

package models

type User struct {
	ID    int    `db:"id"`
	Email string `db:"email"`
	Name  string `db:"name"`
}

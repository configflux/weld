package models

type Item struct {
	ID      int     `db:"id"`
	Title   string  `db:"title"`
	Price   float64 `db:"price"`
	OwnerID int     `db:"owner_id"`
}

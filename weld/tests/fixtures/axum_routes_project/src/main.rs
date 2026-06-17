// A route-emitting fixture for the axum strategy (ADR 0071 / criterion
// 3). It exercises the axum handler-registration callsite grammar the
// strategy recognises: single-method `.route("/p", get(h))` builders and
// chained `.route("/p", get(h).post(h2))` method routers. Kept
// intentionally small and static.
use axum::{Router, routing::{get, post, delete}};

fn app() -> Router {
    Router::new()
        // Single-method builders on distinct paths.
        .route("/health", get(health))
        .route("/users", post(create_user))
        // Method chaining: one path, two HTTP methods.
        .route("/users/:id", get(show_user).delete(remove_user))
        // A commented-out registration must NOT mint a route.
        // .route("/disabled", get(disabled))
        // Wildcard capture path, taken verbatim.
        .route("/assets/*path", get(serve_asset))
}

async fn health() -> &'static str {
    "ok"
}

async fn create_user() {}

async fn show_user() {}

async fn remove_user() {}

async fn serve_asset() {}

#[tokio::main]
async fn main() {
    let _app = app();
}

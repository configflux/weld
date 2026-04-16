FROM golang:1.22-alpine AS build
WORKDIR /app
COPY go.mod ./
COPY . .
RUN go build -o /migrate ./cmd/migrate

FROM alpine:3.19
COPY --from=build /migrate /migrate
CMD ["/migrate"]

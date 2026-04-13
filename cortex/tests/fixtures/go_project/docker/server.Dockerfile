FROM golang:1.22-alpine AS build
WORKDIR /app
COPY go.mod ./
RUN go mod download
COPY . .
RUN go build -o /server ./cmd/server

FROM alpine:3.19
COPY --from=build /server /server
CMD ["/server"]

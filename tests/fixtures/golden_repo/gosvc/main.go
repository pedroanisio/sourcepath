package main

import "fmt"

func greet(name string) string {
	return "hello " + name
}

func main() {
	fmt.Println(greet("golden"))
}

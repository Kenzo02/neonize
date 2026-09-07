package main

import "C"

//export GetVersion
func GetVersion() *C.char {
	version := "0.4.3.post1"
	return C.CString(version)
}

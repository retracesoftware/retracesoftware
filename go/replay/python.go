package replay

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
)

const pythonImportProbe = "import retracesoftware.stream as s; assert getattr(s, '_BIND_OPEN_TAG', None) == '__bind__'"

func pythonCommand(name string, args ...string) *exec.Cmd {
	cmd := exec.Command(name, args...)
	cmd.Env = pythonEnv(cmd.Environ())
	return cmd
}

func pythonEnv(env []string) []string {
	pythonPaths, mesonSkips := checkoutPythonPaths()
	if len(pythonPaths) == 0 {
		return env
	}

	out := prependEnvPath(env, "PYTHONPATH", pythonPaths)
	if len(mesonSkips) > 0 {
		out = prependEnvPath(out, "MESONPY_EDITABLE_SKIP", mesonSkips)
	}
	return out
}

func prependEnvPath(env []string, key string, paths []string) []string {
	prefix := strings.Join(paths, string(os.PathListSeparator))
	out := make([]string, 0, len(env)+1)
	replaced := false
	envPrefix := key + "="
	for _, kv := range env {
		if strings.HasPrefix(kv, envPrefix) {
			value := strings.TrimPrefix(kv, envPrefix)
			if value == "" {
				out = append(out, envPrefix+prefix)
			} else {
				out = append(out, envPrefix+prefix+string(os.PathListSeparator)+value)
			}
			replaced = true
			continue
		}
		out = append(out, kv)
	}
	if !replaced {
		out = append(out, envPrefix+prefix)
	}
	return out
}

func checkoutPythonPaths() ([]string, []string) {
	src := checkoutSrcDir()
	if src == "" {
		return nil, nil
	}

	root := filepath.Dir(src)
	pythonPaths := []string{src}
	var mesonSkips []string

	buildDirs, _ := filepath.Glob(filepath.Join(root, "build", "cp*"))
	for _, buildDir := range buildDirs {
		info, err := os.Stat(buildDir)
		if err != nil || !info.IsDir() {
			continue
		}
		mesonSkips = append(mesonSkips, buildDir)
		for _, rel := range []string{
			filepath.Join("cpp", "cursor"),
			filepath.Join("cpp", "functional"),
			filepath.Join("cpp", "stream"),
			filepath.Join("cpp", "utils"),
		} {
			extDir := filepath.Join(buildDir, rel)
			if info, err := os.Stat(extDir); err == nil && info.IsDir() {
				pythonPaths = append(pythonPaths, extDir)
			}
		}
	}

	return pythonPaths, mesonSkips
}

func checkoutSrcDir() string {
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		return ""
	}
	src := filepath.Clean(filepath.Join(filepath.Dir(file), "..", "..", "src"))
	if _, err := os.Stat(filepath.Join(src, "retracesoftware", "__main__.py")); err != nil {
		return ""
	}
	return src
}

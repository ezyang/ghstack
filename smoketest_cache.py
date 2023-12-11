import ghstack.cache

print(ghstack.cache.get("test", "foo"))
ghstack.cache.put("test", "foo", "bar")
print(ghstack.cache.get("test", "foo"))

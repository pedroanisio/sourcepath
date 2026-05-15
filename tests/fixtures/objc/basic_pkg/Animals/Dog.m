#import "Dog.h"

@implementation Dog
{
    NSString *_name;
}

- (instancetype)initWithName:(NSString *)name {
    self = [super init];
    if (self) {
        _name = [name copy];
    }
    return self;
}

- (NSString *)speak {
    Sound *s = [[Sound alloc] initWithText:@"woof"];
    Sound *louder = [s amplify];
    return [NSString stringWithFormat:@"%@ says %@", _name, [louder text]];
}

- (NSString *)name {
    return _name;
}

- (NSInteger)bumpAge:(NSInteger)current by:(NSInteger)delta {
    return current + delta;
}

- (id)copyWithZone:(NSZone *)zone {
    return [[Dog allocWithZone:zone] initWithName:_name];
}
@end

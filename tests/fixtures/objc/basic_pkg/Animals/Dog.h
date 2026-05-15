#import "Animal.h"
#import "Sound.h"

@interface Dog : Animal <NSCopying>
- (instancetype)initWithName:(NSString *)name;
- (NSInteger)bumpAge:(NSInteger)current by:(NSInteger)delta;
@end
